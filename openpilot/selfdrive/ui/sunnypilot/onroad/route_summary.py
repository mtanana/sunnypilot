"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pyray as rl

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui import UI_BORDER_SIZE
from openpilot.selfdrive.ui.onroad.driver_state import BTN_SIZE
from openpilot.selfdrive.ui.sunnypilot.onroad.developer_ui import get_bottom_dev_ui_offset
from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG
from openpilot.selfdrive.ui.sunnypilot.nav_status import NavState, NavStatus
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import LEFT_MARGIN, format_distance
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

DM_ICON_GAP = 20
STACK_GAP = 20
ROW_HEIGHT = 56
PADDING_V = 14
PADDING_H = 24
FONT_SIZE = 40
# below this the card would be unreadable, so it is dropped rather than shrunk further
MIN_SCALE = 0.6

BACKGROUND = rl.Color(0, 0, 0, 140)
PRIMARY = rl.Color(255, 255, 255, 255)
SECONDARY = rl.Color(255, 255, 255, 217)
ETA_COLOR = rl.Color(0x2f, 0xc4, 0x6e, 0xff)


def format_remaining_time(seconds: float) -> str:
  minutes = max(0, round(seconds / 60))
  if minutes < 60:
    return f"{minutes} min"
  return f"{minutes // 60} hr {minutes % 60} min"


def plan_layout(top: float, bottom: float, row_count: int) -> tuple[int, float, float] | None:
  """(rows kept, scale, y) for the summary card, or None when the rail has no room.

  Top-anchored: the card tucks under whatever the nav stack put above it (the quiet chip,
  or the expanded card while one is up) instead of floating up from the driver monitoring
  icon, which is not necessarily drawn at all. When the rail is crowded the card scales
  down, then sheds its distance row, rather than overlapping a neighbor.
  """
  for n in (row_count, row_count - 1):
    ideal_height = ROW_HEIGHT * n + 2 * PADDING_V
    scale = min(1.0, (bottom - top) / ideal_height)
    if scale >= MIN_SCALE:
      return n, scale, top
  return None


# the arrival time is computed here, not in the message, so the clock stays current between route updates
class RouteSummaryRenderer:
  def __init__(self):
    self.nav_status = NavStatus()
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_semi = gui_app.font(FontWeight.SEMI_BOLD)
    self._tzid: str = ""
    self._tz: ZoneInfo | None = None

  def update(self) -> None:
    self.nav_status.update()

  # the device clock is UTC with no system timezone, so a bare datetime.now() renders the
  # arrival in UTC; navd looks up the destination's zone when it stores a route
  def _destination_tz(self) -> ZoneInfo | None:
    tzid = self.nav_status.destination_timezone
    if tzid != self._tzid:
      self._tzid = tzid
      self._tz = None
      if tzid:
        try:
          self._tz = ZoneInfo(tzid)
        except (ZoneInfoNotFoundError, ValueError):
          cloudlog.error(f"route summary: unknown timezone {tzid!r}, falling back to device time")
    return self._tz

  def render(self, rect: rl.Rectangle, stack_bottom: float | None = None) -> None:
    status = self.nav_status
    if not status.allow_navigation or not status.show_route_summary or status.state != NavState.ACTIVE:
      return

    msg = ui_state.sm['navigationd']
    rows = [
      (format_remaining_time(msg.timeRemaining), self._font_bold, PRIMARY),
      (format_distance(msg.distanceRemaining, ui_state.is_metric), self._font_semi, SECONDARY),
    ]
    # before the first GPS fix the wall clock can still be at its boot value, so no arrival time
    if status.gps_locked:
      # aware arithmetic in UTC, converted last, so a DST change before arrival is honored
      eta = (datetime.now(UTC) + timedelta(seconds=float(msg.timeRemaining))).astimezone(self._destination_tz())
      # %-I is glibc-only; strip the %I zero-pad by hand so non-glibc platforms agree
      rows.append((eta.strftime('%I:%M %p').lstrip('0').lower(), self._font_semi, ETA_COLOR))

    # the space below still ends at the driver monitoring icon, which rides up over the
    # bottom developer UI bar (DriverStateRendererSP)
    bottom = rect.y + rect.height - (UI_BORDER_SIZE + BTN_SIZE) - get_bottom_dev_ui_offset() - DM_ICON_GAP
    top_limit = (stack_bottom if stack_bottom is not None else rect.y) + STACK_GAP
    plan = plan_layout(top_limit, bottom, len(rows))
    if plan is None:
      return
    kept, scale, y = plan
    if kept < len(rows):
      rows = [row for row in rows if row[2] is not SECONDARY]

    font_size = int(FONT_SIZE * scale)
    row_height = ROW_HEIGHT * scale
    sizes = [measure_text_cached(font, text, font_size) for text, font, _ in rows]
    column_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    width = max(column_width, max(size.x for size in sizes) + 2 * PADDING_H * scale)
    height = row_height * len(rows) + 2 * PADDING_V * scale

    box = rl.Rectangle(rect.x + LEFT_MARGIN, y, width, height)
    rl.draw_rectangle_rounded(box, 0.175, 10, BACKGROUND)

    y = box.y + PADDING_V * scale
    for (text, font, color), size in zip(rows, sizes, strict=True):
      rl.draw_text_ex(font, text, rl.Vector2(box.x + (width - size.x) / 2, y + (row_height - size.y) / 2), font_size, 0, color)
      y += row_height
