import base64
import fcntl
import gzip
import json
import socket
import struct
from openpilot.sunnypilot.sunnylink.api import SunnylinkApi, UNREGISTERED_SUNNYLINK_DONGLE_ID
from openpilot.common.params import Params, ParamKeyType
from openpilot.common.version import is_prebuilt


def get_sunnylink_status(params=None) -> tuple[bool, bool, bool]:
  """Get the status of Sunnylink on the device. Returns a tuple of (is_sunnylink_enabled, is_registered)."""
  params = params or Params()
  is_sunnylink_enabled = params.get_bool("SunnylinkEnabled")
  is_registered = params.get("SunnylinkDongleId") not in (None, UNREGISTERED_SUNNYLINK_DONGLE_ID)
  is_on_temporary_fault = params.get_bool("SunnylinkTempFault")
  return is_sunnylink_enabled, is_registered, is_on_temporary_fault


def sunnylink_ready(params=None) -> bool:
  """Check if the device is ready to communicate with Sunnylink. That means it is enabled and registered."""
  params = params or Params()
  is_sunnylink_enabled, is_registered, is_on_temporary_fault = get_sunnylink_status(params)
  return is_sunnylink_enabled and is_registered and not is_on_temporary_fault


def use_sunnylink_uploader(params) -> bool:
  """Check if the device is ready to use Sunnylink and the uploader is enabled."""
  return not params.get_bool("NetworkMetered") and sunnylink_ready(params) and params.get_bool("EnableSunnylinkUploader")


def sunnylink_need_register(params=None) -> bool:
  """Check if the device needs to be registered with Sunnylink."""
  params = params or Params()
  is_sunnylink_enabled, is_registered, is_on_temporary_fault = get_sunnylink_status(params)
  return is_sunnylink_enabled and not is_registered and not is_on_temporary_fault


def register_sunnylink():
  """Register the device with Sunnylink if it is enabled."""
  extra_args = {}

  if not Params().get_bool("SunnylinkEnabled"):
    print("Sunnylink is not enabled. Exiting.")
    exit(0)

  if not is_prebuilt():
    extra_args = {
      "verbose": True,
      "timeout": 60
    }

  try:
    sunnylink_id = SunnylinkApi(None).register_device(None, **extra_args)
    print(f"SunnyLinkId: {sunnylink_id}")
  except Exception:
    Params().put_bool("SunnylinkTempFault", True, block=True)
    raise


def get_api_token():
  """Get the API token for the device."""
  params = Params()
  sunnylink_dongle_id = params.get("SunnylinkDongleId")
  sunnylink_api = SunnylinkApi(sunnylink_dongle_id)
  token = sunnylink_api.get_token()
  print(f"API Token: {token}")


def get_param_as_byte(param_name: str, params=None, get_default=False) -> bytes | None:
  """Get a parameter as bytes. Returns None if the parameter does not exist."""
  params = params or Params()
  param = params.get(param_name) if not get_default else params.get_default_value(param_name)

  if param is None:
    return None

  param_type = params.get_type(param_name)
  return _to_bytes(param, param_type)


def _to_bytes(param: bytes, param_type: ParamKeyType) -> bytes | None:
  """Convert a parameter value to bytes based on its type."""
  if param_type == ParamKeyType.BYTES:
    return bytes(param)
  elif param_type == ParamKeyType.JSON:
    return json.dumps(param).encode('utf-8')
  return str(param).encode('utf-8')


def save_param_from_base64_encoded_string(param_name: str, base64_encoded_data: str, is_compressed=False) -> None:
  """Save a parameter from bytes. Overwrites the parameter if it already exists."""
  params = Params()
  # Find real param name (with correct casing)
  param_type = params.get_type(param_name)
  value = base64.b64decode(base64_encoded_data)

  if is_compressed:
    value = gzip.decompress(value)

  # We convert to string anything that isn't bytes first. We later transform further.
  param_value = _convert_param_to_type(value, param_type)
  params.put(param_name, param_value, block=True)


def _convert_param_to_type(value: bytes, param_type: ParamKeyType) -> bytes | str | int | float | bool | dict | None:
  """
  Convert a byte value to the specified param type. Used internally when getting a Param to convert it to the right type.
  If this method looks familiar, it's because on SP we have a similar one in openpilot/sunnypilot/car/__init__.py.
  """

  # We convert to string anything that isn't bytes first. We later transform further.
  if param_type == ParamKeyType.BYTES:
    return value

  decoded = value.decode('utf-8')

  if param_type == ParamKeyType.STRING:
    return decoded
  elif param_type == ParamKeyType.BOOL:
    return decoded.lower() in ('true', '1', 'yes')
  elif param_type == ParamKeyType.INT:
    return int(decoded)
  elif param_type == ParamKeyType.FLOAT:
    return float(decoded)
  elif param_type == ParamKeyType.TIME:
    return str(decoded)
  elif param_type == ParamKeyType.JSON:
    return json.loads(decoded)

  return decoded


# destinationd's page: served on wlan0, which is 192.168.43.1 itself while the device tethers
DEVICE_PAGE_PORT = 5050
TETHERING_IP = "192.168.43.1"
DEVICE_PAGE_PLACEHOLDER = "{device_page}"
SIOCGIFADDR = 0x8915


def wlan_ipv4(interface: str = "wlan0") -> str:
  """The interface's IPv4 address, empty while it has none."""
  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    try:
      packed = fcntl.ioctl(sock.fileno(), SIOCGIFADDR, struct.pack("256s", interface.encode()[:15]))
    except OSError:
      return ""
  return socket.inet_ntoa(packed[20:24])


def device_page_hint(ip: str | None = None) -> str:
  """Where the device page answers right now: its wifi address, the tethering one while tethering."""
  ip = wlan_ipv4() if ip is None else ip
  tethering = f"http://{TETHERING_IP}:{DEVICE_PAGE_PORT}"
  if ip == TETHERING_IP:
    return f"{tethering} (Wifi Tethering)"
  if ip:
    return f"http://{ip}:{DEVICE_PAGE_PORT}"
  return f"the device's wifi address once it joins a network, or {tethering} once you enable Wifi Tethering"


def fill_device_page(schema: dict, hint: str) -> dict:
  """Replace the page placeholder in every panel, section and item description with where the page is now."""
  def fill(node):
    description = node.get("description")
    if isinstance(description, str) and DEVICE_PAGE_PLACEHOLDER in description:
      node["description"] = description.replace(DEVICE_PAGE_PLACEHOLDER, hint)

  def walk(items):
    for item in items:
      fill(item)
      walk(item.get("sub_items", []))

  for panel in schema.get("panels", []):
    fill(panel)
    walk(panel.get("items", []))
    for section in panel.get("sections", []):
      fill(section)
      walk(section.get("items", []))
      for sub_panel in section.get("sub_panels", []):
        fill(sub_panel)
        walk(sub_panel.get("items", []))
  return schema
