"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.common.constants import CV
from openpilot.selfdrive.ui.mici.onroad.torque_bar import TorqueBar
from openpilot.selfdrive.ui.sunnypilot.onroad.developer_ui import DeveloperUiRenderer, DeveloperUiState, get_bottom_dev_ui_offset
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_banner import (
  NavBannerRenderer, PILL_BOTTOM, PILL_HEIGHT, PILL_WIDTH, PILL_X, PILL_Y,
)
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import NavIndicatorRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.road_name import RoadNameRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.route_summary import RouteSummaryRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.rocket_fuel import RocketFuel
from openpilot.selfdrive.ui.sunnypilot.onroad.speed_limit import SpeedLimitRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.smart_cruise_control import SmartCruiseControlRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.turn_signal import TurnSignalController
from openpilot.selfdrive.ui.sunnypilot.onroad.circular_alerts import CircularAlertsRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.speed_renderer import SpeedRenderer
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.onroad.hud_renderer import HudRenderer, UI_CONFIG, FONT_SIZES, COLORS, CRUISE_DISABLED_CHAR
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached

SLA_ACTIVE_COLOR = rl.Color(0x91, 0x9b, 0x95, 0xff)


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.developer_ui = DeveloperUiRenderer()
    self.nav_indicator = NavIndicatorRenderer()
    self.nav_banner = NavBannerRenderer(self.nav_indicator)
    self.road_name_renderer = RoadNameRenderer()
    self.route_summary = RouteSummaryRenderer()
    self.rocket_fuel = RocketFuel()
    self.speed_limit_renderer = SpeedLimitRenderer()
    self.smart_cruise_control_renderer = SmartCruiseControlRenderer()
    self.turn_signal_controller = TurnSignalController()
    self.circular_alerts_renderer = CircularAlertsRenderer()
    self.speed_renderer = SpeedRenderer()
    self._torque_bar = TorqueBar(scale=3.0, always=True)

    self.pcm_cruise_speed: bool = True
    self.show_icbm_status: bool = False
    self.icbm_active_counter: int = 0
    self.speed_cluster: float = 0.0
    self.speed_conv: float = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH

  def _update_state(self) -> None:
    # nav state advances before the early return below: the banner decides whether the
    # speed displays reflow this frame, and it must not stall when carState is missing
    self.nav_banner.update_state()

    if ui_state.sm.recv_frame["carState"] < ui_state.started_frame:
      return

    if ui_state.CP_SP is not None:
      self.pcm_cruise_speed = ui_state.CP_SP.pcmCruiseSpeed
    self.speed_conv = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self.speed_cluster = ui_state.sm['carState'].cruiseState.speedCluster * self.speed_conv

    super()._update_state()
    self.road_name_renderer.update()
    self.route_summary.update()
    self.speed_limit_renderer.update()
    self.smart_cruise_control_renderer.update()
    self.turn_signal_controller.update()
    self.circular_alerts_renderer.update()
    self.speed_renderer.update()

  def user_interacting(self) -> bool:
    # taps on the nav chip or the banner toggle their expansion and must not also open the sidebar
    return super().user_interacting() or self.nav_indicator.is_pressed or self.nav_banner.is_pressed

  def _get_icbm_status(self):
    if not self.pcm_cruise_speed and ui_state.sm['carControl'].enabled:
      if round(self.set_speed) != round(self.speed_cluster):
        self.icbm_active_counter = 3 * gui_app.target_fps  # 3 seconds usually
      elif self.icbm_active_counter > 0:
        self.icbm_active_counter -= 1
    else:
      self.icbm_active_counter = 0

    self.show_icbm_status = self.icbm_active_counter > 0

  def _speed_colors(self) -> tuple[rl.Color, rl.Color]:
    """(max_color, set_speed_color), shared by the set-speed box and the consolidated pill."""
    long_plan_sp = ui_state.sm['longitudinalPlanSP']
    long_override = ui_state.sm['carControl'].cruiseControl.override

    max_color = COLORS.GREY
    set_speed_color = COLORS.DARK_GREY
    if self.is_cruise_set:
      set_speed_color = COLORS.WHITE
      if long_plan_sp.speedLimit.assist.active:
        set_speed_color = SLA_ACTIVE_COLOR if long_override else rl.Color(0, 0xff, 0, 0xff)
        max_color = SLA_ACTIVE_COLOR if long_override else rl.Color(0x80, 0xd8, 0xa6, 0xff)
      else:
        if ui_state.status == UIStatus.ENGAGED:
          max_color = COLORS.ENGAGED
        elif ui_state.status == UIStatus.DISENGAGED:
          max_color = COLORS.DISENGAGED
        elif ui_state.status == UIStatus.OVERRIDE:
          max_color = COLORS.OVERRIDE
    return max_color, set_speed_color

  @staticmethod
  def speed_displays_consolidated(banner_showing: bool, hide_v_ego: bool) -> bool:
    """The banner sits where the current speed lives, so while it is up the set-speed box
    and the center speed consolidate into the left pill. When the current speed display is
    switched off there is nothing to consolidate and the box keeps its place."""
    return banner_showing and not hide_v_ego

  @staticmethod
  def nav_summary_anchor(banner_showing: bool, hide_v_ego: bool, cruise_available: bool,
                         chip_bottom: float, rect_y: float = 0.0) -> float:
    """Where the route summary tucks: under the chip normally, under the consolidated pill
    (or the box the reflow left in place) while the banner is up."""
    if not banner_showing:
      return chip_bottom
    if not hide_v_ego and cruise_available:
      return rect_y + PILL_BOTTOM
    return rect_y + 45 + UI_CONFIG.set_speed_height

  def _nav_stack_bottom(self, rect: rl.Rectangle) -> float:
    return self.nav_summary_anchor(self.nav_banner.showing, ui_state.hide_v_ego_ui,
                                   self.is_cruise_available, self.nav_indicator.stack_bottom, rect.y)

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    if self.speed_displays_consolidated(self.nav_banner.showing, ui_state.hide_v_ego_ui):
      # the pill drawn from _draw_current_speed carries MAX while the banner is up
      return

    self._get_icbm_status()

    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45

    set_speed_rect = rl.Rectangle(x, y, set_speed_width, UI_CONFIG.set_speed_height)
    rl.draw_rectangle_rounded(set_speed_rect, 0.35, 10, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(set_speed_rect, 0.35, 10, 6, COLORS.BORDER_TRANSLUCENT)

    max_color, set_speed_color = self._speed_colors()

    max_str_size = 60 if self.show_icbm_status else 40
    max_str_y = 15 if self.show_icbm_status else 27

    max_text = str(round(self.speed_cluster)) if self.show_icbm_status else tr("MAX")
    max_text_width = measure_text_cached(self._font_semi_bold, max_text, max_str_size).x
    rl.draw_text_ex(
      self._font_semi_bold,
      max_text,
      rl.Vector2(x + (set_speed_width - max_text_width) / 2, y + max_str_y),
      max_str_size,
      0,
      max_color,
    )

    set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.set_speed))
    speed_text_width = measure_text_cached(self._font_bold, set_speed_text, FONT_SIZES.set_speed).x
    rl.draw_text_ex(
      self._font_bold,
      set_speed_text,
      rl.Vector2(x + (set_speed_width - speed_text_width) / 2, y + 77),
      FONT_SIZES.set_speed,
      0,
      set_speed_color,
    )

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    if self.speed_displays_consolidated(self.nav_banner.showing, ui_state.hide_v_ego_ui):
      self._draw_speed_pill(rect)
      return
    self.speed_renderer.render(rect)

  def _draw_speed_pill(self, rect: rl.Rectangle) -> None:
    """Current speed and MAX consolidated into one left-rail pill while the banner is up.
    When cruise is unavailable the pill keeps the set-speed box's height and shows only
    the current speed."""
    self._get_icbm_status()
    height = PILL_HEIGHT if self.is_cruise_available else UI_CONFIG.set_speed_height
    pill = rl.Rectangle(rect.x + PILL_X, rect.y + PILL_Y, PILL_WIDTH, height)
    rl.draw_rectangle_rounded(pill, 0.24, 10, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(pill, 0.24, 10, 6, COLORS.BORDER_TRANSLUCENT)

    def centered(font, text, y, size, color):
      width = measure_text_cached(font, text, size).x
      rl.draw_text_ex(font, text, rl.Vector2(pill.x + (PILL_WIDTH - width) / 2, y), size, 0, color)

    centered(self._font_bold, str(round(self.speed_renderer.speed)), pill.y + 33, 100, COLORS.WHITE)
    unit = tr("km/h") if ui_state.is_metric else tr("mph")
    centered(self._font_medium, unit, pill.y + 135, 35, rl.Color(180, 180, 180, 255))

    if not self.is_cruise_available:
      return
    rl.draw_rectangle_rec(rl.Rectangle(pill.x + 24, pill.y + 188, PILL_WIDTH - 48, 3), rl.Color(255, 255, 255, 50))
    max_color, set_speed_color = self._speed_colors()
    max_text = str(round(self.speed_cluster)) if self.show_icbm_status else tr("MAX")
    centered(self._font_semi_bold, max_text, pill.y + 203, 34, max_color)
    set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.set_speed))
    centered(self._font_bold, set_speed_text, pill.y + 241, 66, set_speed_color)

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)

    if ui_state.torque_bar:
      torque_rect = rect
      if ui_state.developer_ui in (DeveloperUiState.BOTTOM, DeveloperUiState.BOTH):
        torque_rect = rl.Rectangle(rect.x, rect.y, rect.width, rect.height - get_bottom_dev_ui_offset())
      self._torque_bar.render(torque_rect)

    self.developer_ui.render(rect)
    self.nav_indicator.render(rect)
    self.nav_banner.render(rect)
    self.route_summary.render(rect, self._nav_stack_bottom(rect))
    self.road_name_renderer.render(rect)
    self.speed_limit_renderer.render(rect)
    self.smart_cruise_control_renderer.render(rect)
    self.turn_signal_controller.render(rect)
    self.circular_alerts_renderer.render(rect)
    self.rocket_fuel.render(rect, ui_state.sm)
