"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from math import cos, radians, sin

import math
import time

import pyray as rl

from openpilot.cereal import log
from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG
from openpilot.selfdrive.ui.sunnypilot.nav_status import ROUTE_FAILURE_THRESHOLD, NavStatus
from openpilot.selfdrive.ui.sunnypilot.onroad.transient_nav import (
  ChipMode, TransientNav, TransientNavState, chip_mode, pick_upcoming_maneuver,
)
from openpilot.sunnypilot.navd.constants import NAV_CV
from openpilot.sunnypilot.navd.helpers import ROUNDABOUT_TYPES
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

LaneChangeState = log.LaneChangeState

# directly below the set speed box (x+60, y+45, set_speed_height tall)
LEFT_MARGIN = 60
TOP_OFFSET = 45 + UI_CONFIG.set_speed_height + 20

BOX_HEIGHT = 84
ICON_WIDTH = 44

CHIP_ICON_SIZE = 40
CHIP_FONT_SIZE = 32
CHIP_GAP = 14
CHIP_DIM = rl.Color(255, 255, 255, 170)

# the glyph of the turn the route slowdown brakes for breathes while the target is held
PULSE_PERIOD = 2.0  # s
PULSE_DEPTH = 0.35
CHIP_SEARCHING = rl.Color(255, 255, 255, 110)

LANE_INACTIVE = rl.Color(255, 255, 255, 80)

BACKGROUND = rl.Color(0, 0, 0, 140)
BAD = rl.Color(0xf2, 0x4b, 0x4b, 0xff)
TURN_COLOR = rl.Color(255, 255, 255, 255)
ROAD_DIM = rl.Color(255, 255, 255, 80)  # the carriageway not taken, on exits, forks and merges

# degrees clockwise from straight ahead; u-turns and roundabouts have their own glyphs
ARROW_ANGLES = {
  'straight': 0, 'none': 0,
  'slightRight': 45, 'right': 90, 'sharpRight': 135,
  'slightLeft': -45, 'left': -90, 'sharpLeft': -135,
}


def assist_pulse(now: float | None = None) -> float:
  """Alpha scale for the glyph of a maneuver the car is acting on: a slow breathe over a third of it."""
  t = time.monotonic() if now is None else now
  return 1.0 - PULSE_DEPTH * (0.5 - 0.5 * math.cos(2 * math.pi * t / PULSE_PERIOD))


def slowing_for_turn() -> bool:
  return bool(ui_state.sm['longitudinalPlanSP'].smartCruiseControl.navTurn.active)


def assist_ready() -> bool:
  """The route lane change delay stands ready to take the signaled exit or merge."""
  nav = ui_state.sm['navigationd']
  lane_change = ui_state.sm['modelV2'].meta.laneChangeState
  return bool(nav.laneChangeAutoConfirm) and str(nav.laneChangeDirection) in ('left', 'right') \
    and lane_change in (LaneChangeState.off, LaneChangeState.preLaneChange)


# one breath for both: the car is about to act on this maneuver
def assist_pulsing() -> bool:
  return slowing_for_turn() or assist_ready()


def lane_direction(lane) -> str:
  if lane.active and lane.activeDirection:
    return lane.activeDirection
  return lane.directions[0] if len(lane.directions) else 'straight'


def format_distance(distance_m: float, is_metric: bool) -> str:
  distance_m = max(0.0, distance_m)
  if is_metric:
    if distance_m < 1000:
      return f"{round(distance_m / 10) * 10:.0f} m"
    km = distance_m / 1000
    return f"{km:.0f} km" if km >= 10 else f"{km:.1f} km"
  feet = distance_m * NAV_CV.FEET_PER_METER
  if feet < 1000:
    return f"{round(feet / 50) * 50:.0f} ft"
  miles = distance_m / NAV_CV.METERS_PER_MILE
  return f"{miles:.0f} mi" if miles >= 10 else f"{miles:.1f} mi"


def _draw_flag(cx: float, cy: float, color: rl.Color, size: float = ICON_WIDTH, banner: bool = True) -> None:
  """The destination flag; without the banner, a planted pole (the no-fix searching stage)."""
  pole_w = max(3.0, size * 0.12)
  height = size * 1.05
  x = cx - size / 2
  top = cy - height / 2

  if banner:
    rl.draw_rectangle_rec(rl.Rectangle(x, top, pole_w, height), color)
    rl.draw_rectangle_rec(rl.Rectangle(x + pole_w, top, size - pole_w, height * 0.45), color)
    return

  # butt joints: this draws outside the glyph cache in a translucent color, so overlaps double-blend
  ball_r = size * 0.14
  base_h = max(3.0, size * 0.10)
  base_w = size * 0.52
  pole_cx = x + pole_w / 2
  rl.draw_circle_v(rl.Vector2(pole_cx, top + ball_r), ball_r, color)
  rl.draw_rectangle_rec(rl.Rectangle(x, top + 2 * ball_r, pole_w, height - 2 * ball_r - base_h), color)
  rl.draw_rectangle_rec(rl.Rectangle(pole_cx - base_w / 2, top + height - base_h, base_w, base_h), color)


def _draw_pin(cx: float, cy: float, modifier: str, color: rl.Color, size: float = ICON_WIDTH) -> None:
  """The destination: a planted pin; beside the road, on its side, when the maneuver says which."""
  if modifier not in ('left', 'right'):
    _draw_flag(cx, cy, color, size, banner=False)
    return
  sgn = 1.0 if modifier == 'right' else -1.0
  half_stroke = size * 0.11
  rl.draw_rectangle_rec(rl.Rectangle(cx - half_stroke, cy - size * 0.5, 2 * half_stroke, size), ROAD_DIM)
  _draw_flag(cx + sgn * size * 0.34, cy + size * 0.06, color, size * 0.62, banner=False)


def _stroke(x: float, y: float, heading_deg: float, length: float, half_stroke: float, color: rl.Color) -> None:
  # draw_rectangle_pro rotates about the origin point placed at rec.x/rec.y; the rectangle's
  # local +y axis points down unrotated, so heading + 180 sends it along the travel direction
  rl.draw_rectangle_pro(rl.Rectangle(x, y, 2 * half_stroke, length),
                        rl.Vector2(half_stroke, 0), heading_deg + 180, color)


def _cap(x: float, y: float, heading_deg: float, half_stroke: float, color: rl.Color) -> None:
  rl.draw_circle_sector(rl.Vector2(x, y), half_stroke, heading_deg - 180, heading_deg, 16, color)


def _head(x: float, y: float, heading_deg: float, radius: float, color: rl.Color) -> tuple[float, float]:
  """Arrowhead whose back edge overlaps the stroke it caps; only safe inside the opaque glyph cache."""
  rad = radians(heading_deg)
  dx, dy = sin(rad), -cos(rad)
  px, py = -dy, dx
  head_len = radius * 1.5
  half_w = radius * 0.85
  bx, by = x - dx * radius * 0.1, y - dy * radius * 0.1
  tip = rl.Vector2(bx + dx * head_len, by + dy * head_len)
  left = rl.Vector2(bx + px * half_w, by + py * half_w)
  right = rl.Vector2(bx - px * half_w, by - py * half_w)
  # rlgl culls by winding; drawing both keeps every heading visible
  rl.draw_triangle(tip, left, right, color)
  rl.draw_triangle(tip, right, left, color)
  return dx, dy


def _draw_arrow(cx: float, cy: float, angle_deg: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  half = size / 2
  head_radius = size * 0.36
  half_stroke = size * 0.11
  # screen y grows downward, so straight ahead is (0, -1)
  rad = radians(angle_deg)
  dx, dy = sin(rad), -cos(rad)

  shaft_len = size - head_radius * 1.5
  _stroke(cx - dx * half, cy - dy * half, angle_deg, shaft_len, half_stroke, color)
  _head(cx + dx * (half - head_radius * 1.5), cy + dy * (half - head_radius * 1.5), angle_deg, head_radius, color)


# every glyph enters from the bottom (current travel) and the arrowhead leaves along the maneuver
def _draw_turn(cx: float, cy: float, angle_deg: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  if angle_deg == 0:
    _draw_arrow(cx, cy, 0, color, size)
    return

  half_stroke = size * 0.11
  a = int(abs(angle_deg))
  sgn = 1.0 if angle_deg > 0 else -1.0
  arc_radius = {45: 0.20, 90: 0.20, 135: 0.16}[a] * size
  stem_x = cx - sgn * {45: 0.18, 90: 0.28, 135: 0.22}[a] * size
  arc_y = cy + {45: 0.10, 90: -0.06, 135: -0.16}[a] * size
  leg_len = {45: 0.30, 90: 0.22, 135: 0.28}[a] * size

  rl.draw_rectangle_rec(rl.Rectangle(stem_x - half_stroke, arc_y, 2 * half_stroke, cy + size * 0.5 - arc_y), color)
  center = rl.Vector2(stem_x + sgn * arc_radius, arc_y)
  # ring angles run clockwise from +x in screen coords
  if sgn > 0:
    rl.draw_ring(center, arc_radius - half_stroke, arc_radius + half_stroke, 180, 180 + a, 24, color)
  else:
    rl.draw_ring(center, arc_radius - half_stroke, arc_radius + half_stroke, -a, 0, 24, color)

  rad = radians(a)
  leg_x = stem_x + sgn * arc_radius * (1 - cos(rad))
  leg_y = arc_y - arc_radius * sin(rad)
  _stroke(leg_x, leg_y, angle_deg, leg_len, half_stroke, color)
  head_radius = size * 0.22
  dx, dy = sin(radians(angle_deg)), -cos(radians(angle_deg))
  _head(leg_x + dx * leg_len, leg_y + dy * leg_len, angle_deg, head_radius, color)


# exits keep the continuing carriageway vertical; a plain fork leans both branches apart
def _draw_fork(cx: float, cy: float, sgn: float, color: rl.Color, size: float = ICON_WIDTH, exit_ramp: bool = False) -> None:
  half_stroke = size * 0.11
  split_y = cy + size * 0.04
  branch_len = size * 0.52

  # dim carriageway first, so the bright path paints over it
  through_angle = 0.0 if exit_ramp else -sgn * 25.0
  through_rad = radians(through_angle)
  _stroke(cx, split_y, through_angle, branch_len, half_stroke, ROAD_DIM)
  _cap(cx + sin(through_rad) * branch_len, split_y - cos(through_rad) * branch_len, through_angle, half_stroke, ROAD_DIM)

  rl.draw_rectangle_rec(rl.Rectangle(cx - half_stroke, split_y, 2 * half_stroke, cy + size * 0.5 - split_y), color)
  rl.draw_circle_v(rl.Vector2(cx, split_y), half_stroke, color)

  taken_angle = sgn * 35.0
  rad = radians(taken_angle)
  dx, dy = sin(rad), -cos(rad)
  _stroke(cx, split_y, taken_angle, branch_len, half_stroke, color)
  _head(cx + dx * branch_len, split_y + dy * branch_len, taken_angle, size * 0.22, color)


def _draw_merge(cx: float, cy: float, sgn: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  half_stroke = size * 0.11
  ramp_heading = sgn * 38.0
  arc_radius = size * 0.20
  highway_x = cx + sgn * size * 0.18
  arc_y = cy + size * 0.04
  top_y = cy - size * 0.36
  bottom = cy + size * 0.5

  rl.draw_rectangle_rec(rl.Rectangle(highway_x - half_stroke, arc_y, 2 * half_stroke, bottom - arc_y), ROAD_DIM)

  # the ring's tangent is vertical at the join, so no corner pokes past either stroke
  center = rl.Vector2(highway_x - sgn * arc_radius, arc_y)
  rad = radians(abs(ramp_heading))
  if sgn > 0:
    rl.draw_ring(center, arc_radius - half_stroke, arc_radius + half_stroke, 0, abs(ramp_heading), 20, color)
    arc_end = rl.Vector2(center.x + arc_radius * cos(rad), arc_y + arc_radius * sin(rad))
  else:
    rl.draw_ring(center, arc_radius - half_stroke, arc_radius + half_stroke, 180 - abs(ramp_heading), 180, 20, color)
    arc_end = rl.Vector2(center.x - arc_radius * cos(rad), arc_y + arc_radius * sin(rad))

  ramp_len = (bottom - arc_end.y) / cos(rad)
  _stroke(arc_end.x, arc_end.y, ramp_heading + 180, ramp_len, half_stroke, color)
  _cap(arc_end.x - sin(radians(ramp_heading)) * ramp_len, arc_end.y + cos(rad) * ramp_len,
       ramp_heading + 180, half_stroke, color)

  rl.draw_rectangle_rec(rl.Rectangle(highway_x - half_stroke, top_y, 2 * half_stroke, arc_y - top_y), color)
  _head(highway_x, top_y, 0, size * 0.22, color)


def _draw_uturn(cx: float, cy: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  radius = size * 0.30
  half_stroke = size * 0.11
  arc_cy = cy - size * 0.12
  # ring angles run clockwise from +x in screen coords: 180..360 is the upper half
  rl.draw_ring(rl.Vector2(cx, arc_cy), radius - half_stroke, radius + half_stroke, 180, 360, 24, color)

  leg_len = size * 0.46
  rl.draw_rectangle_rec(rl.Rectangle(cx + radius - half_stroke, arc_cy, 2 * half_stroke, leg_len), color)
  rl.draw_rectangle_rec(rl.Rectangle(cx - radius - half_stroke, arc_cy, 2 * half_stroke, leg_len * 0.5), color)
  _head(cx - radius, arc_cy + leg_len * 0.5, 180, size * 0.24, color)


def _draw_roundabout(cx: float, cy: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  radius = size * 0.26
  half_stroke = size * 0.10
  ring_cy = cy
  rl.draw_ring(rl.Vector2(cx, ring_cy), radius - half_stroke, radius + half_stroke, 0, 360, 32, color)

  # generic glyph; the banner text carries the exit number
  rl.draw_rectangle_rec(rl.Rectangle(cx - half_stroke, ring_cy + radius - half_stroke, 2 * half_stroke,
                                     cy + size * 0.5 - (ring_cy + radius - half_stroke)), color)
  exit_h = size * 0.14
  rl.draw_rectangle_rec(rl.Rectangle(cx - half_stroke, ring_cy - radius - exit_h, 2 * half_stroke, exit_h + half_stroke), color)
  _head(cx, ring_cy - radius - exit_h, 0, size * 0.22, color)


def _draw_maneuver_shapes(cx: float, cy: float, maneuver_type: str, modifier: str, color: rl.Color, size: float) -> None:
  angle = ARROW_ANGLES.get(modifier, 0)
  if maneuver_type == 'arrive':
    _draw_pin(cx, cy, modifier, color, size)
  elif any(t in maneuver_type for t in ROUNDABOUT_TYPES):
    _draw_roundabout(cx, cy, color, size)
  elif modifier == 'uturn':
    _draw_uturn(cx, cy, color, size)
  # a sideless fork or merge has no branch to brighten, so those fall through to the arrow
  elif maneuver_type in ('off ramp', 'fork') and angle != 0:
    _draw_fork(cx, cy, 1.0 if angle > 0 else -1.0, color, size, exit_ramp=maneuver_type == 'off ramp')
  elif maneuver_type == 'merge' and angle != 0:
    _draw_merge(cx, cy, 1.0 if angle > 0 else -1.0, color, size)
  else:
    _draw_turn(cx, cy, angle, color, size)


# the device has no MSAA, and translucent colors double-blend wherever strokes overlap, so each
# glyph is drawn once, opaque and GLYPH_SS times its size, into a RenderTexture and blitted
# scaled down with bilinear filtering; the caller's color, alpha included, tints it as one piece
GLYPH_SS = 4
_GLYPH_PAD = 1.5  # glyphs poke past their size box (the flag's height, the roundabout's arrow)
_glyph_cache: dict[tuple, rl.RenderTexture] = {}


def _glyph_texture(key: tuple, draw, side: int) -> rl.RenderTexture:
  tex = _glyph_cache.get(key)
  if tex is None:
    tex = rl.load_render_texture(side, side)
    # the GL scissor box survives BeginTextureMode, and the road view's box is the screen,
    # which on mici (240 px) is shorter than this texture and would clip the glyph's top
    rl.rl_disable_scissor_test()
    rl.begin_texture_mode(tex)
    rl.clear_background(rl.BLANK)
    draw()
    rl.end_texture_mode()
    rl.rl_enable_scissor_test()
    rl.set_texture_filter(tex.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
    _glyph_cache[key] = tex
  return tex


def _blit_glyph(tex: rl.RenderTexture, cx: float, cy: float, color: rl.Color, dest_side: float) -> None:
  # drawing over a transparent ground leaves the texture premultiplied, so the tint is folded in
  tint = rl.Color(color.r * color.a // 255, color.g * color.a // 255, color.b * color.a // 255, color.a)
  src = rl.Rectangle(0, 0, tex.texture.width, -tex.texture.height)
  dest = rl.Rectangle(cx - dest_side / 2, cy - dest_side / 2, dest_side, dest_side)
  rl.begin_blend_mode(rl.BlendMode.BLEND_ALPHA_PREMULTIPLY)
  rl.draw_texture_pro(tex.texture, src, dest, rl.Vector2(0, 0), 0, tint)
  rl.end_blend_mode()


def _draw_maneuver_icon(cx: float, cy: float, maneuver_type: str, modifier: str, color: rl.Color, size: float) -> None:
  side = int(size * GLYPH_SS * _GLYPH_PAD)
  key = (maneuver_type, modifier, side)
  tex = _glyph_texture(key, lambda: _draw_maneuver_shapes(side / 2, side / 2, maneuver_type, modifier,
                                                          TURN_COLOR, size * GLYPH_SS), side)
  _blit_glyph(tex, cx, cy, color, size * _GLYPH_PAD)


def draw_lane_glyph(cx: float, cy: float, direction: str, color: rl.Color, size: float) -> None:
  side = int(size * GLYPH_SS * _GLYPH_PAD)
  key = ('lane', direction, side)

  def _draw():
    if direction == 'uturn':
      _draw_uturn(side / 2, side / 2, TURN_COLOR, size * GLYPH_SS)
    else:
      _draw_turn(side / 2, side / 2, ARROW_ANGLES.get(direction, 0), TURN_COLOR, size * GLYPH_SS)

  _blit_glyph(_glyph_texture(key, _draw, side), cx, cy, color, size * _GLYPH_PAD)


# the quiet chip: next maneuver or route status; while TransientNav is expanded the
# top-center banner (nav_banner) takes over and this draws nothing
class NavIndicatorRenderer(Widget):
  def __init__(self):
    super().__init__()
    self.nav_status = NavStatus()
    self.transient = TransientNav()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    self._mode = ChipMode.HIDDEN
    self._update_frame = -1
    self._pulse = False
    # the route summary lays out below this
    self.stack_bottom: float = 0.0

  @property
  def mode(self) -> ChipMode:
    return self._mode

  def update_state(self) -> None:
    """Once per SubMaster frame; the HUD calls it before its own layout so the banner reflows the same frame."""
    if self._update_frame == ui_state.sm.frame:
      return
    self._update_frame = ui_state.sm.frame
    self.nav_status.update()
    self._mode = chip_mode(self.nav_status)
    msg = ui_state.sm['navigationd']
    self.transient.update(self._mode == ChipMode.LIVE, msg.allManeuvers, msg.audioCueId, msg.audioCueStage,
                          off_route=msg.routeState != 'onRoute')
    self._pulse = assist_pulsing()

  def _update_state(self) -> None:
    self.update_state()

  def _handle_mouse_release(self, mouse_pos) -> None:
    super()._handle_mouse_release(mouse_pos)
    self.transient.on_tap()

  def _render_status_chip(self, box: rl.Rectangle, color: rl.Color) -> None:
    rl.draw_rectangle_rounded(box, 0.35, 10, BACKGROUND)
    _draw_flag(box.x + box.width / 2, box.y + box.height / 2, color, CHIP_ICON_SIZE)

  def _render_chip(self, box: rl.Rectangle, maneuver: tuple[str, str, float], dimmed: bool = False, pulse: bool = False) -> None:
    maneuver_type, modifier, distance = maneuver
    rl.draw_rectangle_rounded(box, 0.35, 10, BACKGROUND)

    if dimmed:
      # off route: no distance, it would count down to a maneuver the car is not approaching
      _draw_maneuver_icon(box.x + box.width / 2, box.y + box.height / 2,
                          maneuver_type, modifier, CHIP_SEARCHING, CHIP_ICON_SIZE)
      return

    text = format_distance(distance, ui_state.is_metric)
    text_size = measure_text_cached(self._font, text, CHIP_FONT_SIZE)
    span = CHIP_ICON_SIZE + CHIP_GAP + text_size.x
    x = box.x + (box.width - span) / 2
    cy = box.y + box.height / 2
    icon_color = rl.Color(255, 255, 255, int(CHIP_DIM.a * assist_pulse())) if pulse else CHIP_DIM
    _draw_maneuver_icon(x + CHIP_ICON_SIZE / 2, cy, maneuver_type, modifier, icon_color, CHIP_ICON_SIZE)
    rl.draw_text_ex(self._font, text, rl.Vector2(x + CHIP_ICON_SIZE + CHIP_GAP, cy - text_size.y / 2),
                    CHIP_FONT_SIZE, 0, CHIP_DIM)

  def _render(self, rect: rl.Rectangle) -> None:
    top = rect.y + TOP_OFFSET
    self.stack_bottom = top
    self.set_rect(rl.Rectangle(0, 0, 0, 0))
    if self._mode == ChipMode.HIDDEN:
      return

    width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + LEFT_MARGIN

    if self._mode == ChipMode.SEARCHING:
      # the phone and the head unit report the route search; the rail waits for a route
      return
    if self._mode == ChipMode.FAILURE:
      # no touch target: informational only
      self._render_status_chip(rl.Rectangle(x, top, width, BOX_HEIGHT), BAD)
      self.stack_bottom = top + BOX_HEIGHT
      return

    if self.transient.state in (TransientNavState.APPROACH, TransientNavState.PINNED):
      return

    msg = ui_state.sm['navigationd']
    if msg.routeState == 'rerouting':
      # the searching flag doubles as the reroute cue; failing recomputes turn it red
      failing = msg.routeFailures >= ROUTE_FAILURE_THRESHOLD
      self._render_status_chip(rl.Rectangle(x, top, width, BOX_HEIGHT), BAD if failing else CHIP_SEARCHING)
      self.stack_bottom = top + BOX_HEIGHT
      return

    maneuver = pick_upcoming_maneuver(msg.allManeuvers)
    if maneuver is None:
      return

    box = rl.Rectangle(x, top, width, BOX_HEIGHT)
    self._render_chip(box, maneuver, dimmed=msg.routeState == 'offRoute', pulse=self._pulse)
    self.stack_bottom = top + box.height
    self.set_rect(box)
