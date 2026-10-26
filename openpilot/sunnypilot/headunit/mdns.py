"""
Optional mDNS service advertisement for headunitd.

Registers a _headunit._tcp.local. record so the Flutter app (multicast_dns package)
can discover the comma without knowing its IP. Requires the `zeroconf` Python package,
which is present in the sunnypilot pip environment (used by athena WiFi pairing).
If it is missing, mDNS is silently skipped — the app falls back to subnet scan.
"""
import socket

from openpilot.common.swaglog import cloudlog

_zc = None
_info = None


def _local_ip() -> str:
  """Return the primary non-loopback IPv4 address."""
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
      s.connect(("8.8.8.8", 80))
      return s.getsockname()[0]
  except Exception:
    return "127.0.0.1"


def start(port: int, instance: str = "sunnyhead") -> None:
  """Register the service. No-op if zeroconf is unavailable or registration fails."""
  global _zc, _info
  try:
    from zeroconf import Zeroconf, ServiceInfo  # type: ignore[import]
    ip = _local_ip()
    _info = ServiceInfo(
      "_headunit._tcp.local.",
      f"{instance}._headunit._tcp.local.",
      addresses=[socket.inet_aton(ip)],
      port=port,
      properties={b"version": b"1"},
      server=f"{socket.gethostname()}.local.",
    )
    _zc = Zeroconf()
    _zc.register_service(_info)
    cloudlog.info(f"headunit mDNS: registered {instance} at {ip}:{port}")
  except ImportError:
    cloudlog.info("headunit mDNS: zeroconf not available, skipping mDNS advertisement")
  except Exception:
    cloudlog.exception("headunit mDNS: registration failed")


def stop() -> None:
  """Unregister the service cleanly."""
  global _zc, _info
  if _zc is not None and _info is not None:
    try:
      _zc.unregister_service(_info)
      _zc.close()
    except Exception:
      pass
    finally:
      _zc = None
      _info = None
