#!/usr/bin/env python3
"""Convert openpilot rlogs / qlogs into a .shlog file for sunnyhead's offline log analyzer.

  python -m openpilot.sunnypilot.headunit.log_export /data/media/0/realdata/<route>--5 -o drive.shlog
  python -m openpilot.sunnypilot.headunit.log_export <route>             # every local segment of a route
  python -m openpilot.sunnypilot.headunit.log_export rlog.zst qlog.zst   # explicit files, in order
  python -m openpilot.sunnypilot.headunit.log_export --dump drive.shlog  # summarise an export

Every scalar field of every logged service becomes a channel named like the capnp path
("carState.vEgo", "carControl.actuators.accel", "pandaStates[0].ignitionLine"). Short lists are
flattened by index; text fields and lists of named events (onroadEvents) become text channels.
CAN frames are decoded with the car's DBC: "sendcan<bus>.<msg>.<signal>" is what openpilot
commanded, "can<bus>.<msg>.<signal>" what was on the bus.

File format (whole file gzip compressed, little-endian):
  "SHLG" | u32 version | u32 header_len | header JSON | zero pad to 4 bytes | channel data
Each header channel {"name", "kind": num|bool|enum, "n", "off", "labels"?} owns
float32 t[n] then float32 v[n] at data + off. t is seconds since the first event. Runs of
equal values are stored as their two endpoints only, which renders identically.
"""
import argparse
import array
import glob
import gzip
import json
import math
import os
import re
import struct
import sys

MAGIC = b"SHLG"
FORMAT_VERSION = 1
REALDATA = "/data/media/0/realdata"

NUM_TYPES = {"int8", "int16", "int32", "uint8", "uint16", "uint32", "float32", "float64"}
# 64-bit ints are timestamps and ids that float32 can't hold anyway
SKIP_NAME_RE = re.compile(r"DEPRECATED$|^deprecated$|MonoTime$|[tT]imestamp|^frameId|FrameId$")
MAX_TEXT_CHANGES = 5000

# Video, raw sensor blobs, logs and per-process stats: large and not useful on a timeline.
DEFAULT_SKIP_SERVICES = frozenset({
  "initData", "sentinel", "boot", "logMessage", "errorLogMessage", "thumbnail", "qcomGnss", "ubloxGnss", "ubloxRaw",
  "rawAudioData", "touch", "procLog", "managerState", "clocks", "operatingSystemLog", "uiDebug",
  "carParams", "carParamsSP", "navRoute", "navInstruction",
  "narrowRoadEncodeIdx", "cabinEncodeIdx", "wideRoadEncodeIdx", "qNarrowRoadEncodeIdx",
  "livestreamNarrowRoadEncodeIdx", "livestreamWideRoadEncodeIdx", "livestreamCabinEncodeIdx",
  "narrowRoadEncodeData", "cabinEncodeData", "wideRoadEncodeData", "qNarrowRoadEncodeData",
  "livestreamNarrowRoadEncodeData", "livestreamWideRoadEncodeData", "livestreamCabinEncodeData",
  "customReservedRawData0", "customReservedRawData1", "customReservedRawData2",
})

PRESETS = {
  "all": {"services": None, "max_list": 8},
  # longitudinal troubleshooting: planner -> controls -> car -> CAN, plus what it reacted to
  "long": {"services": frozenset({
    "carState", "carControl", "carOutput", "controlsState", "longitudinalPlan", "radarState", "selfdriveState",
    "onroadEvents", "onroadEventsSP", "livePose", "modelV2", "accelerometer", "longitudinalPlanSP", "carStateSP",
    "carControlSP", "selfdriveStateSP", "deviceState", "pandaStates", "userBookmark", "bookmarkButton",
  }), "max_list": 3},
}

# CAN messages decoded with --can auto, by brand. Others get no CAN unless asked for.
CAN_PRESETS = {
  "subaru": ("Throttle", "Transmission", "CVT", "Brake_Status", "Brake_Pedal", "Wheel_Speeds", "G_Sensor",
             "CruiseControl", "Cruise_Buttons", "ES_Distance", "ES_Status", "ES_Brake", "ES_DashStatus"),
}

MARK_SERVICES = ("userBookmark", "bookmarkButton")
# DBC placeholders for bits nobody has identified ("Signal1"); skipped by --can auto
UNNAMED_SIGNAL_RE = re.compile(r"^Signal\d+$")

K_NUM, K_BOOL, K_ENUM, K_TEXT, K_STRUCT, K_LIST = range(6)
KIND_NAMES = {K_NUM: "num", K_BOOL: "bool", K_ENUM: "enum"}


def _enum_labels(schema) -> tuple[list[str], dict[str, int]]:
  ordinals = dict(schema.enumerants)
  labels = [""] * (max(ordinals.values()) + 1 if ordinals else 0)
  for name, i in ordinals.items():
    labels[i] = name
  return labels, ordinals


def _enum_ord(value, ordinals: dict[str, int]) -> int:
  raw = getattr(value, "raw", None)
  if raw is not None:
    return int(raw)
  return ordinals.get(str(value), -1)


def _scalar_kind(type_name: str) -> int | None:
  if type_name in NUM_TYPES:
    return K_NUM
  if type_name == "bool":
    return K_BOOL
  if type_name == "enum":
    return K_ENUM
  return None


class _FieldPlan:
  """How to read one struct field. Struct sub-plans are built lazily from the first reader seen."""
  __slots__ = ("name", "kind", "elem", "labels", "ordinals", "named", "sub")

  def __init__(self, name: str, kind: int, elem: int | None = None):
    self.name = name
    self.kind = kind
    self.elem = elem
    self.labels: list[str] | None = None
    self.ordinals: dict[str, int] = {}
    self.named = False  # list of structs carrying a `name` enum (onroadEvents)
    self.sub: _StructPlan | None = None

  @staticmethod
  def build(schema, name: str) -> '_FieldPlan | None':
    if SKIP_NAME_RE.search(name):
      return None
    field = schema.fields[name]
    proto = field.proto
    if proto.which() == "group":
      return _FieldPlan(name, K_STRUCT)

    type_name = proto.slot.type.which()
    kind = _scalar_kind(type_name)
    if kind is not None:
      plan = _FieldPlan(name, kind)
      if kind == K_ENUM:
        plan.labels, plan.ordinals = _enum_labels(field.schema)
      return plan
    if type_name == "text":
      return _FieldPlan(name, K_TEXT)
    if type_name == "struct":
      return _FieldPlan(name, K_STRUCT)
    if type_name != "list":
      return None

    elem_name = proto.slot.type.list.elementType.which()
    elem = _scalar_kind(elem_name)
    if elem is not None:
      plan = _FieldPlan(name, K_LIST, elem)
      if elem == K_ENUM:
        plan.labels, plan.ordinals = _enum_labels(field.schema.elementType)
      return plan
    if elem_name == "struct":
      plan = _FieldPlan(name, K_LIST, K_STRUCT)
      elem_fields = field.schema.elementType.fields
      if "name" in elem_fields:
        name_proto = elem_fields["name"].proto
        plan.named = name_proto.which() == "slot" and name_proto.slot.type.which() == "enum"
      return plan
    return None


class _StructPlan:
  __slots__ = ("fields", "union")

  def __init__(self, schema):
    self.fields = [p for p in (_FieldPlan.build(schema, n) for n in schema.non_union_fields) if p is not None]
    self.union = {n: p for n in schema.union_fields if (p := _FieldPlan.build(schema, n)) is not None}


class _Channel:
  __slots__ = ("kind", "labels", "t", "v", "_last", "_pending_t", "_pending")

  def __init__(self, kind: int, labels: list[str] | None = None):
    self.kind = kind
    self.labels = labels
    self.t = array.array("f")
    self.v = array.array("f")
    self._last: float | None = None
    self._pending_t = 0.0
    self._pending = False

  def add(self, t: float, v: float) -> None:
    last = self._last
    if last is not None and (v == last or (v != v and last != last)):
      # inside a run of equal values: only its last sample matters
      self._pending_t = t
      self._pending = True
      return
    if self._pending:
      self.t.append(self._pending_t)
      self.v.append(last)
      self._pending = False
    self.t.append(t)
    self.v.append(v)
    self._last = v

  def finish(self) -> None:
    if self._pending:
      self.t.append(self._pending_t)
      self.v.append(self._last)
      self._pending = False


class _TextChannel:
  __slots__ = ("t", "v", "truncated")

  def __init__(self):
    self.t: list[float] = []
    self.v: list[str] = []
    self.truncated = False

  def add(self, t: float, s: str) -> None:
    if self.v and self.v[-1] == s:
      return
    if len(self.v) >= MAX_TEXT_CHANGES:
      self.truncated = True
      return
    self.t.append(round(t, 4))
    self.v.append(s)


class _CanDecoder:
  def __init__(self, dbc_name: str, messages: tuple[str, ...] | None, named_only: bool = False):
    from opendbc.can.dbc import DBC
    dbc = DBC(dbc_name)
    self.dbc_name = dbc_name
    self.msgs = {}
    found = set()
    for addr, msg in dbc.msgs.items():
      if messages is not None and msg.name not in messages:
        continue
      # SignalType.DEFAULT only: counters and checksums are noise on a plot
      sigs = [s for s in msg.sigs.values() if s.type == 0 and s.name not in ("CHECKSUM", "COUNTER")
              and not (named_only and UNNAMED_SIGNAL_RE.match(s.name))]
      if sigs:
        self.msgs[addr] = (msg.name, sigs)
        found.add(msg.name)
    self.missing = sorted(set(messages or ()) - found)

  def decode(self, prefix: str, frames, t: float, exporter: 'LogExporter') -> None:
    for frame in frames:
      src = frame.src
      if src >= 128:  # panda TX echoes; sendcan already has what was sent
        continue
      entry = self.msgs.get(frame.address)
      if entry is None:
        continue
      msg_name, sigs = entry
      dat = frame.dat
      base = f"{prefix}{src}.{msg_name}."
      for sig in sigs:
        raw = sig.get_raw_value(dat)
        if sig.is_signed:
          raw -= ((raw >> (sig.size - 1)) & 0x1) * (1 << sig.size)
        ch = exporter.channel(base + sig.name, K_BOOL if sig.size == 1 else K_NUM)
        if ch is not None:
          ch.add(t, raw * sig.factor + sig.offset)


def _plain(value):
  """JSON-safe scalar for header metadata."""
  if isinstance(value, float):
    return value if math.isfinite(value) else None
  if isinstance(value, (bool, int, str)) or value is None:
    return value
  return str(value)


def _flatten_dict(d, prefix="", out=None) -> dict:
  out = {} if out is None else out
  for key, value in d.items():
    path = f"{prefix}{key}"
    if isinstance(value, dict):
      _flatten_dict(value, path + ".", out)
    elif isinstance(value, list):
      if len(value) <= 8 and all(not isinstance(v, (dict, list)) for v in value):
        out[path] = [_plain(v) for v in value]
    else:
      out[path] = _plain(value)
  return out


def dbc_for_fingerprint(fingerprint: str) -> str | None:
  try:
    from opendbc.car.values import PLATFORMS
  except ImportError:
    return None
  platform = PLATFORMS.get(fingerprint)
  if platform is None:
    return None
  return platform.config.dbc_dict.get("pt")


class LogExporter:
  def __init__(self, services: frozenset[str] | None = None, skip_services: frozenset[str] = DEFAULT_SKIP_SERVICES,
               include: list[str] | None = None, exclude: list[str] | None = None, max_list: int = 8, max_hz: float = 0.0,
               can: str = "auto", dbc: str | None = None):
    from openpilot.cereal import log
    self._event_plan = _StructPlan(log.Event.schema)
    self.services = services
    self.skip_services = skip_services
    self.include = [re.compile(p) for p in include or ()]
    self.exclude = [re.compile(p) for p in exclude or ()]
    self.max_list = max_list
    self.min_dt = 1.0 / max_hz if max_hz > 0 else 0.0
    self.can_mode = can
    self.dbc = dbc

    self.channels: dict[str, _Channel] = {}
    self.texts: dict[str, _TextChannel] = {}
    self.marks: list[dict] = []
    self.meta: dict = {}
    self.car_params: dict = {}
    self.warnings: list[str] = []
    self._rejected: set[str] = set()
    self._last_kept: dict[str, float] = {}
    self._counts: dict[str, int] = {}
    self._can: _CanDecoder | None = None
    self._can_ready = can == "none"
    self.t0: int | None = None
    self.t_end = 0.0

  # ---- channel sinks

  def _wanted(self, path: str) -> bool:
    if self.include and not any(p.search(path) for p in self.include):
      return False
    return not any(p.search(path) for p in self.exclude)

  def channel(self, path: str, kind: int, labels: list[str] | None = None) -> _Channel | None:
    ch = self.channels.get(path)
    if ch is None:
      if path in self._rejected:
        return None
      if not self._wanted(path):
        self._rejected.add(path)
        return None
      ch = self.channels[path] = _Channel(kind, labels)
    return ch

  def _text(self, path: str, t: float, s: str) -> None:
    ch = self.texts.get(path)
    if ch is None:
      if path in self._rejected:
        return
      if not self._wanted(path):
        self._rejected.add(path)
        return
      ch = self.texts[path] = _TextChannel()
    ch.add(t, s)

  # ---- capnp walking

  def _scalar(self, path: str, kind: int, plan: _FieldPlan, value, t: float) -> None:
    ch = self.channel(path, kind, plan.labels if kind == K_ENUM else None)
    if ch is None:
      return
    if kind == K_NUM:
      ch.add(t, float(value))
    elif kind == K_BOOL:
      ch.add(t, 1.0 if value else 0.0)
    else:
      ch.add(t, float(_enum_ord(value, plan.ordinals)))

  def _walk(self, reader, plan: _StructPlan, prefix: str, t: float) -> None:
    for f in plan.fields:
      self._visit(reader, f, prefix, t)
    if plan.union:
      try:
        which = str(reader.which())
      except Exception:  # discriminant from a newer schema
        return
      f = plan.union.get(which)
      if f is not None:
        self._visit(reader, f, prefix, t)

  def _visit(self, reader, f: _FieldPlan, prefix: str, t: float) -> None:
    path = prefix + f.name
    kind = f.kind
    if kind <= K_ENUM:
      self._scalar(path, kind, f, getattr(reader, f.name), t)
    elif kind == K_TEXT:
      self._text(path, t, str(getattr(reader, f.name)))
    elif kind == K_STRUCT:
      value = getattr(reader, f.name)
      if f.sub is None:
        f.sub = _StructPlan(value.schema)
      self._walk(value, f.sub, path + ".", t)
    else:
      items = getattr(reader, f.name)
      if f.named:
        self._text(path, t, ", ".join(sorted(str(e.name) for e in items)))
        return
      n = len(items)
      if n == 0 or n > self.max_list:
        return
      if f.elem == K_STRUCT:
        for i in range(n):
          item = items[i]
          if f.sub is None:
            f.sub = _StructPlan(item.schema)
          self._walk(item, f.sub, f"{path}[{i}].", t)
      else:
        for i in range(n):
          self._scalar(f"{path}[{i}]", f.elem, f, items[i], t)

  # ---- events

  def set_car_params(self, cp) -> None:
    if cp is None or self.car_params:
      return
    try:
      self.car_params = _flatten_dict(cp.to_dict())
    except Exception as e:  # metadata only; never fail an export over it
      self.warnings.append(f"carParams: {e}")
      self.car_params = {"carFingerprint": str(cp.carFingerprint)}
    self._setup_can(str(cp.carFingerprint), str(cp.brand))

  def _setup_can(self, fingerprint: str, brand: str) -> None:
    if self._can_ready:
      return
    self._can_ready = True
    dbc = self.dbc or dbc_for_fingerprint(fingerprint)
    if dbc is None:
      self.warnings.append(f"no DBC for {fingerprint!r}; CAN not decoded (use --dbc)")
      return
    if self.can_mode == "auto":
      messages = CAN_PRESETS.get(brand) or next((m for b, m in CAN_PRESETS.items() if dbc.startswith(b)), None)
      if messages is None:
        return
    elif self.can_mode == "all":
      messages = None
    else:
      messages = tuple(m.strip() for m in self.can_mode.split(",") if m.strip())
    try:
      self._can = _CanDecoder(dbc, messages, named_only=self.can_mode == "auto")
    except Exception as e:
      self.warnings.append(f"DBC {dbc}: {e}")
      return
    if self._can.missing:
      self.warnings.append(f"not in {dbc}: {', '.join(self._can.missing)}")
    self.meta["dbc"] = dbc

  def add(self, evt) -> None:
    mono = evt.logMonoTime
    if self.t0 is None:
      self.t0 = mono
    t = (mono - self.t0) * 1e-9
    if t > self.t_end:
      self.t_end = t
    service = str(evt.which())

    if service in ("can", "sendcan"):
      if not self._can_ready and self.dbc:
        self._setup_can("", "")
      if self._can is not None:
        self._can.decode(service, getattr(evt, service), t, self)
      return
    if service == "carParams":
      self.set_car_params(evt.carParams)
      return
    if service == "initData":
      if "initData" not in self.meta:
        init = evt.initData
        self.meta["initData"] = {k: _plain(str(getattr(init, k))) for k in ("dongleId", "version", "gitBranch", "gitCommit")}
        self.meta["initData"]["deviceType"] = str(init.deviceType)
        wall = int(init.wallTimeNanos)
        if wall > 0:
          self.meta["wallTimeNsAtT0"] = wall - (mono - self.t0)
      return
    if service in MARK_SERVICES:
      self.marks.append({"t": round(t, 4), "label": service})
    if service in self.skip_services or (self.services is not None and service not in self.services):
      return
    plan = self._event_plan.union.get(service)
    if plan is None:
      return
    if self.min_dt:
      last = self._last_kept.get(service)
      if last is not None and t - last < self.min_dt:
        return
      self._last_kept[service] = t
    self._counts[service] = self._counts.get(service, 0) + 1

    valid = self.channel(service + "._valid", K_BOOL)
    if valid is not None:
      valid.add(t, 1.0 if evt.valid else 0.0)
    self._visit(evt, plan, "", t)

  # ---- output

  def build(self) -> tuple[dict, list[bytes]]:
    channels, blobs, off = [], [], 0
    for name in sorted(self.channels):
      ch = self.channels[name]
      ch.finish()
      n = len(ch.t)
      if n == 0:
        continue
      if sys.byteorder != "little":
        ch.t.byteswap()
        ch.v.byteswap()
      entry = {"name": name, "kind": KIND_NAMES[ch.kind], "n": n, "off": off}
      if ch.labels:
        entry["labels"] = ch.labels
      channels.append(entry)
      blobs.append(ch.t.tobytes() + ch.v.tobytes())
      off += 8 * n

    texts = [{"name": k, "t": v.t, "v": v.v, **({"truncated": True} if v.truncated else {})}
             for k, v in sorted(self.texts.items()) if any(v.v)]
    header = {
      "format": "sunnyhead-log",
      "version": FORMAT_VERSION,
      "duration": round(self.t_end, 4),
      "t0MonoNs": self.t0 or 0,
      "carParams": self.car_params,
      "services": dict(sorted(self._counts.items())),
      "channels": channels,
      "texts": texts,
      "marks": self.marks,
      "warnings": self.warnings,
      **self.meta,
    }
    return header, blobs

  def write(self, path: str, source: list[str] | None = None) -> dict:
    header, blobs = self.build()
    if source:
      header["source"] = source
    hdr = json.dumps(header, separators=(",", ":"), allow_nan=False).encode()
    pad = (-(12 + len(hdr))) % 4
    with gzip.open(path, "wb", compresslevel=6) as f:
      f.write(MAGIC + struct.pack("<II", FORMAT_VERSION, len(hdr)) + hdr + b"\0" * pad)
      for blob in blobs:
        f.write(blob)
    return header


def read_shlog(path: str) -> tuple[dict, dict[str, tuple[array.array, array.array]]]:
  with gzip.open(path, "rb") as f:
    dat = f.read()
  if dat[:4] != MAGIC:
    raise ValueError(f"{path}: not a .shlog file")
  _version, hdr_len = struct.unpack_from("<II", dat, 4)
  header = json.loads(dat[12:12 + hdr_len])
  base = 12 + hdr_len + (-(12 + hdr_len)) % 4
  data = {}
  for ch in header["channels"]:
    n, start = ch["n"], base + ch["off"]
    t, v = array.array("f"), array.array("f")
    t.frombytes(dat[start:start + 4 * n])
    v.frombytes(dat[start + 4 * n:start + 8 * n])
    if sys.byteorder != "little":
      t.byteswap()
      v.byteswap()
    data[ch["name"]] = (t, v)
  return header, data


# ---- input resolution

LOG_NAMES = ("rlog.zst", "rlog.bz2", "rlog", "qlog.zst", "qlog.bz2", "qlog")
SEGMENT_RE = re.compile(r"^(.*)--(\d+)$")


def _log_in_dir(d: str, qlog: bool) -> str | None:
  names = LOG_NAMES[3:] + LOG_NAMES[:3] if qlog else LOG_NAMES
  for name in names:
    p = os.path.join(d, name)
    if os.path.isfile(p):
      return p
  return None


def _segment_key(path: str):
  seg = path if os.path.isdir(path) else os.path.dirname(path)
  m = SEGMENT_RE.match(os.path.basename(os.path.normpath(seg)))
  return (m.group(1), int(m.group(2)), path) if m else (seg, 0, path)


def resolve_inputs(items: list[str], qlog: bool = False) -> list[str]:
  files: list[str] = []
  for item in items:
    if not os.path.exists(item) and os.path.isdir(os.path.join(REALDATA, item)):
      item = os.path.join(REALDATA, item)  # bare segment name on the device
    if os.path.isdir(item):
      fn = _log_in_dir(item, qlog)
      if fn is None:
        raise SystemExit(f"no rlog/qlog in {item}")
      files.append(fn)
    elif os.path.isfile(item):
      files.append(item)
    else:
      local = [d for d in glob.glob(os.path.join(REALDATA, item + "--*")) if SEGMENT_RE.match(os.path.basename(d))]
      found = [fn for d in local if (fn := _log_in_dir(d, qlog))]
      if found:
        files.extend(found)
      else:
        from openpilot.tools.lib.logreader import LogReader, ReadMode
        files.extend(LogReader(item, default_mode=ReadMode.QLOG if qlog else ReadMode.AUTO).logreader_identifiers)
  local_files = [f for f in files if os.path.exists(f)]
  if len(local_files) == len(files):
    files = sorted(files, key=_segment_key)
  return files


def _default_output(files: list[str]) -> str:
  first = _segment_key(files[0])
  name = os.path.basename(os.path.normpath(first[0])) or "export"
  if len(files) == 1:
    return f"{name}--{first[1]}.shlog"
  last = _segment_key(files[-1])
  return f"{name}--{first[1]}-{last[1]}.shlog"


def dump(path: str) -> None:
  header, data = read_shlog(path)
  print(f"{path}: {header['duration']:.1f} s, {len(header['channels'])} channels, {len(header['texts'])} text channels, "
        f"{len(header['marks'])} marks")
  print(f"car: {header['carParams'].get('carFingerprint')}  dbc: {header.get('dbc')}")
  for w in header.get("warnings", []):
    print(f"warning: {w}")
  for ch in header["channels"]:
    t, v = data[ch["name"]]
    finite = [x for x in v if math.isfinite(x)]
    span = f"{min(finite):.4g} .. {max(finite):.4g}" if finite else "nan"
    print(f"  {ch['name']:<60} {ch['kind']:<5} n={ch['n']:<7} {span}")


def main(argv: list[str] | None = None) -> int:
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  ap.add_argument("inputs", nargs="*", help="rlog/qlog files, segment directories, or route names")
  ap.add_argument("-o", "--output", help="output .shlog path (default: <route>--<seg>.shlog)")
  ap.add_argument("--preset", choices=sorted(PRESETS), default="all", help="'long' keeps only longitudinal services")
  ap.add_argument("--services", help="comma-separated services to keep (overrides --preset)")
  ap.add_argument("--include", action="append", help="regex; keep only matching channel paths (repeatable)")
  ap.add_argument("--exclude", action="append", help="regex; drop matching channel paths (repeatable)")
  ap.add_argument("--max-list", type=int, help="flatten lists up to this length (default from preset)")
  ap.add_argument("--max-hz", type=float, default=0.0, help="decimate every service to this rate (0 = keep all)")
  ap.add_argument("--can", default="auto", help="auto | none | all | MSG,MSG,... (decode sendcan/can with the DBC)")
  ap.add_argument("--dbc", help="DBC name, if carParams is missing or wrong")
  ap.add_argument("--qlog", action="store_true", help="prefer qlogs in segment directories")
  ap.add_argument("--dump", metavar="SHLOG", help="print the channels of an existing export and exit")
  args = ap.parse_args(argv)

  if args.dump:
    dump(args.dump)
    return 0
  if not args.inputs:
    ap.error("no inputs")

  from openpilot.tools.lib.logreader import LogReader

  preset = PRESETS[args.preset]
  services = frozenset(s.strip() for s in args.services.split(",")) if args.services else preset["services"]
  exporter = LogExporter(services=services, include=args.include, exclude=args.exclude,
                         max_list=args.max_list if args.max_list is not None else preset["max_list"],
                         max_hz=args.max_hz, can=args.can, dbc=args.dbc)

  files = resolve_inputs(args.inputs, qlog=args.qlog)
  for i, fn in enumerate(files):
    print(f"[{i + 1}/{len(files)}] {fn}", file=sys.stderr)
    lr = LogReader(fn, sort_by_time=True)
    if i == 0:
      exporter.set_car_params(lr.first("carParams"))
    for evt in lr:
      exporter.add(evt)

  out = args.output or _default_output(files)
  header = exporter.write(out, source=[os.path.basename(os.path.dirname(f)) or f for f in files])
  for w in header["warnings"]:
    print(f"warning: {w}", file=sys.stderr)
  print(f"wrote {out}: {header['duration']:.1f} s, {len(header['channels'])} channels, "
        f"{os.path.getsize(out) / 1e6:.1f} MB", file=sys.stderr)
  return 0


if __name__ == "__main__":
  sys.exit(main())
