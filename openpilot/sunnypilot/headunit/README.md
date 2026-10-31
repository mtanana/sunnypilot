# headunitd

Streams sunnypilot state to a head unit, phone or browser on the local network, with
optional H.264 video. Nothing here changes driving behavior; the only core edit is the two
process entries in `openpilot/system/manager/process_config.py`.

## Processes

| Process | Runs | What it does |
|---|---|---|
| `headunitd` | always (unless disabled in config) | HTTP server on port 8090, telemetry, video session state |
| `headunit_encoderd` | only during a video session, onroad, while comma connect isn't streaming | `encoderd --stream` (same binary comma connect uses) |

Idle cost: one Python process at nice 10 waking twice a second. JSON is only built while
a client is listening. The encoder only runs while a `/video` viewer is connected.

## Endpoints

| Endpoint | Notes |
|---|---|
| `GET /` | Test page (static files in `web/`) |
| `GET /status` | Bridge + video state, last stop reason, guard numbers |
| `GET /snapshot` | One telemetry snapshot |
| `GET /telemetry?hz=N` | Server-Sent Events, `data: <json>` per snapshot, N = 0.5..20 |
| `GET /video?camera=road\|wide\|driver&format=framed\|raw` | H.264. Connecting starts a session; 409 + `{"reason": ...}` if refused |
| `POST /video/stop` | Stop the session now |

There's no login. Requests from globally routable addresses get 403 unless
`allow_public_clients` is set; LAN, link-local, hotspot and tailscale (100.64/10) clients are fine.

## Telemetry

SI units (m, m/s, m/s², deg) except `car.vCruise` and `car.vCruiseCluster`, which are km/h
like carState. `isMetric` is the device setting. Non-finite numbers are `null`. `alive` maps each
source service to whether it's currently publishing; offroad most are false.

Geometry frames: `model.laneLines`, `model.roadEdges`, `model.path` are device frame
(x forward, y **right** positive, 17 points). `lead.one.yRel` is **left** positive.

Derived lead values: `lead.timeGapS = dRel / vEgo`, and `lead.desiredDistM` is the
distance openpilot's planner aims for:
`(vEgo² − vLead²) / (2·2.5) + T·vEgo + 6`, T = 1.25 / 1.45 / 1.75 s by personality.

`schema "v"` is bumped on breaking changes.

## Video

- **framed** (default): repeated `[u32 BE length][u8 flags, bit0 = keyframe][u64 BE pts µs][payload]`.
  Payload is Annex B; keyframes start with SPS/PPS. Android: feed each payload to a `video/avc`
  `MediaCodec` as one input buffer (keyframes with `BUFFER_FLAG_KEY_FRAME`). Some decoders want
  SPS/PPS as `csd-0`/`csd-1` first; split them off the first keyframe if yours does.
- **raw**: plain Annex B, for players:
  `ffplay -fflags nobuffer -flags low_delay -f h264 "http://<comma-ip>:8090/video?format=raw"`
- 720p, 20 fps, keyframe every 5 frames, `video_bitrate` per camera (default 2 Mbps).
  `encoderd --stream` encodes every camera even if only one is watched.
- Browser test page: WebCodecs only exists in secure contexts. Over `http://<comma-ip>` enable
  `chrome://flags/#unsafely-treat-insecure-origin-as-secure` with `http://<comma-ip>:8090`.

A session stops (reason in `/status` → `video.lastStopReason`) on: `offroad`, `comma_connect`
(comma connect live view always wins), `thermal`, `model_exec_time`, `frame_drops`,
`session_limit`, `idle` (no viewers for `video_idle_stop_s`), `no_frames`, `user`.
After a load-guard stop, new sessions are refused for `video_cooldown_s` (`cooldown`).

Load guard: frame drops trip as soon as `modelV2.frameDropPerc` passes `guard_frame_drop_perc`
(default 0.5). That line is absolute because selfdrived soft-disables openpilot ("Driving Model
Lagging") above 1%. Model run time trips when its 3 s average passes
`max(guard_model_exec_s, baseline × guard_baseline_headroom)`, where baseline is the 10 s before
video started. Thermal trips at `overheated`.

## Config

Optional `/data/headunit.json` (or `$HEADUNIT_CONFIG`); any field of `HeadUnitConfig` in
`config.py`. Read when the manager starts, so restart after editing. Example:

```json
{"enabled": true, "port": 8090, "video_bitrate": 1500000, "video_max_session_s": 600}
```

## Testing

```bash
pytest openpilot/sunnypilot/headunit
curl -N "http://<comma-ip>:8090/telemetry?hz=2"
```

On a PC, run `python -m openpilot.sunnypilot.headunit.headunitd` next to a replay to get live data.

## Log export (sunnyhead offline analyzer)

`log_export.py` converts rlogs / qlogs into a `.shlog` file that sunnyhead's **Logs** view opens
(drag-and-drop on desktop, Open button anywhere). It runs on the comma or on a PC with this checkout.

```bash
# on the device: every local segment of a route, or one segment
python -m openpilot.sunnypilot.headunit.log_export 0000002a--1a2b3c4d5e --preset long
python -m openpilot.sunnypilot.headunit.log_export 0000002a--1a2b3c4d5e--7
# files / segment dirs anywhere, or anything LogReader accepts (connect URLs, route ids)
python -m openpilot.sunnypilot.headunit.log_export ~/realdata/<route>--4 ~/realdata/<route>--5 -o drive.shlog
python -m openpilot.sunnypilot.headunit.log_export --dump drive.shlog    # list channels
```

- Every scalar field of every service becomes a channel named by its capnp path
  (`carControl.actuators.accel`, `pandaStates[0].ignitionLine`). Lists up to `--max-list` are
  flattened by index; text fields and lists of named events (`onroadEvents`) become text channels;
  `<service>._valid` tracks `valid`. Bookmarks (`userBookmark`, `bookmarkButton`) become marks.
- CAN is decoded with the car's DBC (from carParams, or `--dbc`): `sendcan<bus>.<msg>.<sig>` is what
  openpilot commanded, `can<bus>.<msg>.<sig>` what was received (TX echoes skipped). `--can auto`
  decodes a per-brand list (Subaru: throttle, RPM, brake, ES_* messages), `--can all` the whole DBC.
- `--preset long` keeps only longitudinal services (smaller files for long drives). `--include` /
  `--exclude` filter channel paths by regex, `--max-hz` decimates.
- Format: gzip of `"SHLG" | u32 version | u32 header_len | header JSON | pad to 4 | float32 data`.
  Each channel stores `t[n]` (s since the first event) then `v[n]`; runs of equal values keep only
  their endpoints. See the module docstring.
