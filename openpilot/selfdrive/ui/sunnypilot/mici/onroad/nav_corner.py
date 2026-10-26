"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The mici skin of the transient nav system: a corner glyph, the distance and a small lane
row in the set-speed circle's slot, driven by the same TransientNav machine as the 3X
rail. No card and no street text on a 536x240 screen; audio carries the street names.
Alerts and the set-speed circle take the slot with priority.
"""
from dataclasses import dataclass, field

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.sunnypilot.nav_status import ROUTE_FAILURE_THRESHOLD, NavStatus
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import (
  _draw_flag, _draw_maneuver_icon, assist_pulse, assist_pulsing, draw_lane_glyph, format_distance, lane_direction,
)
from openpilot.selfdrive.ui.sunnypilot.onroad.transient_nav import (
  ChipMode, TransientNav, TransientNavState, chip_mode, flag_raised, pick_upcoming_maneuver,
)
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# centered on the set-speed circle's corner, no wider than the circle within the 476-wide content area
CENTER_X = 70
GLYPH_CY = 58
GLYPH_SIZE = 64
DISTANCE_FONT_SIZE = 34
DISTANCE_TOP = 96
SHADOW_CY = 70
SHADOW_RADIUS = 82

LANE_TOP = 142
LANE_SLOT = 30
LANE_ICON_SIZE = 20

TOUCH_WIDTH = 150
TOUCH_HEIGHT = 180

# mici alpha idiom: 0.9 for live foreground, faint for hints
FULL_ALPHA = 0.9
QUIET_ALPHA = 0.35
SEARCH_ALPHA = 0.35
FAILURE_ALPHA = 0.7

FAILURE_COLOR = (0xf2, 0x4b, 0x4b)
LANE_INACTIVE_ALPHA = 0.35


@dataclass(frozen=True)
class CornerContent:
  """kind is 'maneuver', 'searching' or 'failure'; alpha is the fade target; raised is the
  searching flag's stage (bare pole until the GPS fix)."""
  kind: str
  alpha: float
  maneuver_type: str = ''
  modifier: str = ''
  distance: float | None = None
  lanes: tuple = field(default_factory=tuple)
  raised: bool = True
  pulse: bool = False


def corner_content(state: TransientNavState, mode: ChipMode, msg,
                   lane_guidance: bool, quiet_glyph: bool, raised: bool = True, assist: bool = False) -> CornerContent | None:
  if mode == ChipMode.SEARCHING:
    return CornerContent('searching', SEARCH_ALPHA, raised=raised)
  if mode == ChipMode.FAILURE:
    return CornerContent('failure', FAILURE_ALPHA)
  if mode != ChipMode.LIVE:
    return None
  if msg.routeState == 'rerouting':
    # the searching flag doubles as the reroute cue; failing recomputes turn it red
    if msg.routeFailures >= ROUTE_FAILURE_THRESHOLD:
      return CornerContent('failure', FAILURE_ALPHA)
    return CornerContent('searching', SEARCH_ALPHA)
  maneuver = pick_upcoming_maneuver(msg.allManeuvers)
  if maneuver is None:
    return None
  maneuver_type, modifier, distance = maneuver
  if msg.routeState == 'offRoute':
    # off route: a dim glyph even with the quiet glyph off, and no distance or lanes, which
    # would describe a maneuver the car is not approaching
    return CornerContent('maneuver', QUIET_ALPHA, maneuver_type, modifier)
  if state in (TransientNavState.APPROACH, TransientNavState.PINNED):
    lanes = tuple(msg.lanes) if lane_guidance else ()
    return CornerContent('maneuver', FULL_ALPHA, maneuver_type, modifier, distance, lanes, pulse=assist)
  # the slowdown and the lane hint start before the corner's own approach window, so the car
  # shows what it is acting on
  if state == TransientNavState.QUIET and (quiet_glyph or assist):
    return CornerContent('maneuver', QUIET_ALPHA, maneuver_type, modifier, pulse=assist)
  return None


class MiciNavRenderer(Widget):
  """State changes move a fade target and a FirstOrderFilter walks the alpha there."""

  def __init__(self):
    super().__init__()
    self.nav_status = NavStatus()
    self.transient = TransientNav()
    self._mode = ChipMode.HIDDEN
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    self._alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._content: CornerContent | None = None
    self._drawn: CornerContent | None = None
    self._can_draw = True
    self._update_frame = -1

  @property
  def mode(self) -> ChipMode:
    return self._mode

  @property
  def showing(self) -> bool:
    """Visibly occupied, fade tails included; the DMoji yields on it."""
    return self._alpha_filter.x > 1e-2

  def set_can_draw(self, can_draw: bool) -> None:
    """Alerts and the set-speed circle own the corner while they are up."""
    self._can_draw = can_draw

  def update_state(self) -> None:
    if self._update_frame == ui_state.sm.frame:
      return
    self._update_frame = ui_state.sm.frame
    self.nav_status.update()
    self._mode = chip_mode(self.nav_status)
    msg = ui_state.sm['navigationd']
    self.transient.update(self._mode == ChipMode.LIVE, msg.allManeuvers, msg.audioCueId, msg.audioCueStage,
                          off_route=msg.routeState != 'onRoute')
    self._content = corner_content(self.transient.state, self._mode, msg,
                                   self.nav_status.lane_guidance, self.nav_status.mici_quiet_glyph,
                                   flag_raised(self.nav_status.state), assist_pulsing())

  def _update_state(self) -> None:
    self.update_state()

  def _handle_mouse_release(self, mouse_pos) -> None:
    super()._handle_mouse_release(mouse_pos)
    self.transient.on_tap()

  def _render(self, rect: rl.Rectangle) -> None:
    self.set_rect(rl.Rectangle(0, 0, 0, 0))
    content = self._content if self._can_draw else None
    alpha = self._alpha_filter.update(content.alpha if content is not None else 0.0)
    if content is not None:
      self._drawn = content
    if alpha < 1e-2 or self._drawn is None:
      return

    drawn = self._drawn
    a = min(alpha, 1.0)
    if drawn.pulse:
      a *= assist_pulse()
    cx = rect.x + CENTER_X

    # the set-speed circle's drop shadow
    rl.draw_circle_gradient(rl.Vector2(cx, rect.y + SHADOW_CY), SHADOW_RADIUS,
                            rl.Color(0, 0, 0, int(255 / 2 * a)), rl.BLANK)

    if drawn.kind in ('searching', 'failure'):
      r, g, b = FAILURE_COLOR if drawn.kind == 'failure' else (255, 255, 255)
      _draw_flag(cx, rect.y + GLYPH_CY, rl.Color(r, g, b, int(255 * a)), GLYPH_SIZE * 0.8, banner=drawn.raised)
      return

    color = rl.Color(255, 255, 255, int(255 * a))
    _draw_maneuver_icon(cx, rect.y + GLYPH_CY, drawn.maneuver_type, drawn.modifier, color, GLYPH_SIZE)

    if drawn.distance is not None:
      text = format_distance(drawn.distance, ui_state.is_metric)
      size = measure_text_cached(self._font, text, DISTANCE_FONT_SIZE)
      rl.draw_text_ex(self._font, text, rl.Vector2(cx - size.x / 2, rect.y + DISTANCE_TOP),
                      DISTANCE_FONT_SIZE, 0, color)

    if len(drawn.lanes):
      self._render_lane_row(rect, drawn.lanes, a)

    # a fading remnant takes no taps
    if content is not None and content.kind == 'maneuver':
      self.set_rect(rl.Rectangle(rect.x, rect.y, TOUCH_WIDTH, TOUCH_HEIGHT))

  def _render_lane_row(self, rect: rl.Rectangle, lanes, a: float) -> None:
    n = len(lanes)
    x = max(rect.x + 8 + LANE_SLOT / 2, rect.x + CENTER_X - LANE_SLOT * (n - 1) / 2)
    cy = rect.y + LANE_TOP
    for lane in lanes:
      lane_alpha = a if lane.active else a * LANE_INACTIVE_ALPHA
      color = rl.Color(255, 255, 255, int(255 * lane_alpha))
      draw_lane_glyph(x, cy, lane_direction(lane), color, LANE_ICON_SIZE)
      x += LANE_SLOT
