"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The expanded skin of the transient nav system on the 3X: a top-center card with icon,
distance, street text, a Then chip, and the lane row beneath. Geometry and icons follow the
nav-commacon banner. A tap collapses or unpins it; a hold cancels the route.
"""
from dataclasses import dataclass
from pathlib import Path

import pyray as rl

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.sunnypilot.nav_status import ROUTE_FAILURE_THRESHOLD
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import (
  BAD, LANE_INACTIVE, TURN_COLOR, _draw_flag, assist_pulse, assist_pulsing, draw_lane_glyph, format_distance, lane_direction,
)
from openpilot.selfdrive.ui.sunnypilot.onroad.transient_nav import ChipMode, TransientNavState, pick_upcoming_index
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

BANNER_WIDTH = 1080
BANNER_HEIGHT = 225
BANNER_TOP = 62  # below the road name pill, which ends at y 56
BANNER_RADIUS = 42

ICON_SIZE = 150
ICON_PAD = 30
DISTANCE_FONT_SIZE = 48
DISTANCE_TOP = 166

# the Then chip's width is reserved even when absent, so the street text never reflows
TEXT_X = 233
THEN_WIDTH = 180
STREET_FONT_SIZE = 75
STREET_LINE_SPACING = 84
STREET_AREA_WIDTH = BANNER_WIDTH - TEXT_X - ICON_PAD - THEN_WIDTH

THEN_FONT_SIZE = 53
THEN_ICON_SIZE = 105

LANE_ROW_GAP = 10
LANE_ROW_HEIGHT = 64
LANE_SLOT = 110
LANE_ICON_SIZE = 34

HOLD_CANCEL_SECONDS = 0.65

BACKGROUND = rl.Color(0, 0, 0, 180)
LANE_BACKGROUND = rl.Color(0, 0, 0, 140)
DIVIDER = rl.Color(255, 255, 255, 50)
DIM_TREATMENT = rl.Color(255, 255, 255, 110)

# the speed pill that replaces the set-speed box and the current speed while the banner is up
PILL_X = 60
PILL_Y = 45
PILL_WIDTH = 200
PILL_HEIGHT = 320
PILL_BOTTOM = PILL_Y + PILL_HEIGHT

ICONS_PATH = "../../sunnypilot/selfdrive/assets/navigation"
_ICONS_DIR = Path(__file__).resolve().parents[4] / "sunnypilot" / "selfdrive" / "assets" / "navigation"
ICON_FILES = frozenset(p.stem for p in _ICONS_DIR.glob("direction_*.png"))
DEFAULT_ICON = 'direction_turn_straight'

# navigationd publishes string_to_direction's camelCase modifiers; the files use snake_case
MODIFIER_FILE = {'slightLeft': 'slight_left', 'slightRight': 'slight_right',
                 'sharpLeft': 'sharp_left', 'sharpRight': 'sharp_right'}
TYPE_ALIASES = {'roundabout turn': 'roundabout', 'exit roundabout': 'roundabout', 'exit rotary': 'rotary'}


def icon_name(maneuver_type: str, modifier: str) -> str:
  t = TYPE_ALIASES.get(maneuver_type, maneuver_type).replace(' ', '_')
  m = MODIFIER_FILE.get(modifier, modifier)
  if m and m != 'none':
    candidates = (f"direction_{t}_{m}", f"direction_{t}", f"direction_turn_{m}", f"direction_{m}")
  else:
    candidates = (f"direction_{t}",)
  for name in candidates:
    if name in ICON_FILES:
      return name
  return DEFAULT_ICON


def wrap_two_lines(text: str, max_width: float, measure) -> list[str]:
  words = text.split()
  if not words:
    return []
  lines: list[str] = []
  current = ''
  truncated = False
  for word in words:
    trial = f"{current} {word}" if current else word
    if measure(trial) <= max_width or not current:
      current = trial
    elif not lines:
      lines.append(current)
      current = word
    else:
      truncated = True
      break
  lines.append(current)

  out = []
  for i, line in enumerate(lines):
    if measure(line) > max_width or (truncated and i == len(lines) - 1):
      while line and measure(line + '…') > max_width:
        line = line[:-1].rstrip()
      line += '…'
    out.append(line)
  return out


@dataclass(frozen=True)
class BannerContent:
  maneuver_type: str
  modifier: str
  distance: float
  street: str
  then_type: str | None
  then_modifier: str | None
  lanes: list
  route_state: str = 'onRoute'
  failing: bool = False
  pulse: bool = False


def banner_content(state: TransientNavState, mode: ChipMode, msg, lane_guidance: bool, assist: bool = False) -> BannerContent | None:
  if mode != ChipMode.LIVE or state not in (TransientNavState.APPROACH, TransientNavState.PINNED):
    return None
  idx = pick_upcoming_index(msg.allManeuvers)
  if idx is None:
    return None
  m = msg.allManeuvers[idx]
  # the instruction covers the stretch before Mapbox raises the banner
  street = msg.bannerInstructions or m.instruction
  then = msg.allManeuvers[idx + 1] if len(msg.allManeuvers) > idx + 1 else None
  lanes = list(msg.lanes) if lane_guidance else []
  return BannerContent(m.type, m.modifier, m.distance, street,
                       then.type if then is not None else None,
                       then.modifier if then is not None else None, lanes,
                       route_state=str(msg.routeState),
                       failing=msg.routeFailures >= ROUTE_FAILURE_THRESHOLD,
                       pulse=assist and str(msg.routeState) == 'onRoute')


class NavBannerRenderer(Widget):
  def __init__(self, nav_indicator):
    super().__init__()
    self._nav = nav_indicator
    self._params = Params()
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._content: BannerContent | None = None
    self._press_start: float | None = None
    self._hold_fired = False
    self._update_frame = -1

  @property
  def showing(self) -> bool:
    return self._content is not None

  def update_state(self) -> None:
    if self._update_frame == ui_state.sm.frame:
      return
    self._update_frame = ui_state.sm.frame
    self._nav.update_state()
    self._content = banner_content(self._nav.transient.state, self._nav.mode,
                                   ui_state.sm['navigationd'], self._nav.nav_status.lane_guidance, assist_pulsing())

  def _update_state(self) -> None:
    self.update_state()

  def _handle_mouse_press(self, mouse_pos) -> None:
    self._press_start = rl.get_time()
    self._hold_fired = False

  def _handle_mouse_release(self, mouse_pos) -> None:
    super()._handle_mouse_release(mouse_pos)
    if not self._hold_fired:
      self._nav.transient.on_tap()

  def _cancel_route(self) -> None:
    self._hold_fired = True
    self._params.put('MapboxRoute', '')
    # nav_status polls the param at 1 Hz; clearing its model drops the banner this frame
    self._nav.nav_status.destination = ''
    cloudlog.event("nav route cancelled from banner hold")

  def _check_hold(self) -> None:
    if not self.is_pressed:
      self._press_start = None
      return
    if self._press_start is not None and not self._hold_fired and \
        rl.get_time() - self._press_start >= HOLD_CANCEL_SECONDS:
      self._cancel_route()

  def _render(self, rect: rl.Rectangle) -> None:
    self.set_rect(rl.Rectangle(0, 0, 0, 0))
    content = self._content
    if content is None:
      return

    card = rl.Rectangle(rect.x + (rect.width - BANNER_WIDTH) / 2, rect.y + BANNER_TOP, BANNER_WIDTH, BANNER_HEIGHT)
    rl.draw_rectangle_rounded(card, BANNER_RADIUS / (BANNER_HEIGHT / 2), 16, BACKGROUND)

    icon_cx = card.x + ICON_PAD + ICON_SIZE / 2
    if content.route_state == 'rerouting':
      _draw_flag(icon_cx, card.y + 12 + ICON_SIZE / 2, BAD if content.failing else DIM_TREATMENT, ICON_SIZE * 0.55)
    else:
      icon = gui_app.texture(f"{ICONS_PATH}/{icon_name(content.maneuver_type, content.modifier)}.png", ICON_SIZE, ICON_SIZE)
      if content.route_state == 'offRoute':
        tint = DIM_TREATMENT
      elif content.pulse:
        tint = rl.Color(255, 255, 255, int(255 * assist_pulse()))
      else:
        tint = rl.WHITE
      rl.draw_texture_ex(icon, rl.Vector2(card.x + ICON_PAD, card.y + 12), 0, 1.0, tint)

    # off route, the distance would count toward a maneuver the car is not approaching
    if content.route_state == 'onRoute':
      distance = format_distance(content.distance, ui_state.is_metric)
      size = measure_text_cached(self._font_bold, distance, DISTANCE_FONT_SIZE)
      rl.draw_text_ex(self._font_bold, distance, rl.Vector2(icon_cx - size.x / 2, card.y + DISTANCE_TOP),
                      DISTANCE_FONT_SIZE, 0, rl.WHITE)

    lines = wrap_two_lines(content.street, STREET_AREA_WIDTH,
                           lambda s: measure_text_cached(self._font_bold, s, STREET_FONT_SIZE).x)
    if len(lines) == 1:
      ys = [card.y + (BANNER_HEIGHT - STREET_FONT_SIZE) / 2]
    else:
      ys = [card.y + 30, card.y + 30 + STREET_LINE_SPACING]
    for line, y in zip(lines, ys, strict=False):
      rl.draw_text_ex(self._font_bold, line, rl.Vector2(card.x + TEXT_X, y), STREET_FONT_SIZE, 0, rl.WHITE)

    if content.then_type is not None:
      divider_x = card.x + BANNER_WIDTH - THEN_WIDTH - 8
      rl.draw_rectangle_rec(rl.Rectangle(divider_x, card.y + 23, 2, BANNER_HEIGHT - 46), DIVIDER)
      then_cx = divider_x + 15 + (THEN_WIDTH - 23) / 2
      then_text = "Then"
      size = measure_text_cached(self._font_medium, then_text, THEN_FONT_SIZE)
      rl.draw_text_ex(self._font_medium, then_text, rl.Vector2(then_cx - size.x / 2, card.y + 26),
                      THEN_FONT_SIZE, 0, rl.WHITE)
      then_icon = gui_app.texture(f"{ICONS_PATH}/{icon_name(content.then_type, content.then_modifier or '')}.png",
                                  THEN_ICON_SIZE, THEN_ICON_SIZE)
      rl.draw_texture_ex(then_icon, rl.Vector2(then_cx - THEN_ICON_SIZE / 2, card.y + 90), 0, 1.0, rl.WHITE)

    if len(content.lanes):
      self._render_lane_row(rect, card, content.lanes)

    self.set_rect(card)
    self._check_hold()

  def _render_lane_row(self, rect: rl.Rectangle, card: rl.Rectangle, lanes) -> None:
    n = len(lanes)
    width = LANE_SLOT * n + 70
    row = rl.Rectangle(rect.x + (rect.width - width) / 2, card.y + BANNER_HEIGHT + LANE_ROW_GAP, width, LANE_ROW_HEIGHT)
    rl.draw_rectangle_rounded(row, 0.5, 10, LANE_BACKGROUND)

    cy = row.y + row.height / 2
    x = row.x + width / 2 - LANE_SLOT * (n - 1) / 2
    for lane in lanes:
      draw_lane_glyph(x, cy, lane_direction(lane), TURN_COLOR if lane.active else LANE_INACTIVE, LANE_ICON_SIZE)
      x += LANE_SLOT
