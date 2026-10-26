"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.nav_corner import MiciNavRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.blind_spot_indicators import BlindSpotIndicators
from openpilot.selfdrive.ui.ui_state import ui_state


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.blind_spot_indicators = BlindSpotIndicators()
    self._sp_engaged: bool = False
    self.nav_corner = MiciNavRenderer()

  def _update_state(self) -> None:
    # the nav machine advances before anything can early-return: fades must keep walking
    # even on frames where carState has not arrived yet
    self.nav_corner.update_state()
    super()._update_state()
    self.blind_spot_indicators.update()

    sp_engaged = ui_state.engaged
    if sp_engaged != self._sp_engaged:
      self._chestnut_fade_time = rl.get_time() if sp_engaged else 0
    self._sp_engaged = sp_engaged

  def drawing_top_icons(self) -> bool:
    # the corner shares the top-left slot with the set-speed circle, so the DMoji yields
    # to the nav layer exactly as it yields to the circle
    return super().drawing_top_icons() or self.nav_corner.showing

  def user_interacting(self) -> bool:
    # a tap on the corner pins or collapses it and must not also scroll back home
    return self.nav_corner.is_pressed

  @staticmethod
  def corner_can_draw(can_draw_top_icons: bool, cruise_set: bool, set_speed_alpha: float) -> bool:
    """One occupant of the corner at a time: alerts suppress the nav layer like the top
    icons, and while the set-speed circle is up the corner yields. The circle's alpha only
    walks while cruise is set, so a stale filter value does not hold the slot."""
    return can_draw_top_icons and not (cruise_set and set_speed_alpha > 1e-2)

  def _render(self, rect: rl.Rectangle) -> None:
    # one frame behind the circle's own alpha, which a fade absorbs
    self.nav_corner.set_can_draw(self.corner_can_draw(self._can_draw_top_icons, self.is_cruise_set,
                                                      self._set_speed_alpha_filter.x))
    super()._render(rect)
    self.nav_corner.render(rect)
    self.blind_spot_indicators.render(rect)

  def _has_blind_spot_detected(self) -> bool:

    return self.blind_spot_indicators.detected
