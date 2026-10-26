"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The four's navigation page: the master switch that starts destinationd, the route in
progress, and the choices that influence driving, kept on the device so consent for them
happens in the car. Every other navigation choice (destinations, the token, the phone's
own settings) lives on the phone page that daemon serves, where a real keyboard and
description text exist.
"""
import pyray as rl

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigMultiParamToggle, BigParamControl
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.navigation import nav_status_line
from openpilot.selfdrive.ui.sunnypilot.nav_status import NavStatus
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets.scroller import NavScroller

REFRESH_FRAMES = 20  # params re-read for external writes (athena, the phone page, ssh)


class NavigationInfo(Widget):
  """The status line and the destination, the 3X panel's Status and Destination rows."""

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 360, 180))
    header_color = rl.Color(255, 255, 255, int(255 * 0.9))
    text_color = rl.Color(255, 255, 255, int(255 * 0.9 * 0.65))
    max_width = int(self._rect.width - 20)
    self.status_header = UnifiedLabel(tr("status"), 48, max_width=max_width, text_color=header_color,
                                      font_weight=FontWeight.DISPLAY, shimmer=True)
    self.status_text = UnifiedLabel("", 32, max_width=max_width, text_color=text_color, font_weight=FontWeight.ROMAN, scroll=True)
    self.destination_header = UnifiedLabel(tr("destination"), 48, max_width=max_width, text_color=header_color,
                                           font_weight=FontWeight.DISPLAY, shimmer=True)
    self.destination_text = UnifiedLabel("", 32, max_width=max_width, text_color=text_color, font_weight=FontWeight.ROMAN, scroll=True)

  def _render(self, _):
    self.status_header.set_position(self._rect.x + 20, self._rect.y - 10)
    self.status_header.render()
    self.status_text.set_position(self._rect.x + 20, self._rect.y + 68 - 25)
    self.status_text.render()
    self.destination_header.set_position(self._rect.x + 20, self._rect.y + 114 - 30)
    self.destination_header.render()
    self.destination_text.set_position(self._rect.x + 20, self._rect.y + 161 - 25)
    self.destination_text.render()


class NavigationLayoutMici(NavScroller):
  def __init__(self):
    super().__init__()
    self._nav_status = NavStatus()
    self._frame = 0

    self._info = NavigationInfo()
    self._nav_toggle = BigParamControl("allow navigation", "AllowNavigation")
    self._end_route_btn = BigButton("end route")
    self._end_route_btn.set_click_callback(self._end_route)
    self._desires_toggle = BigParamControl("use route turn desires", "NavDesiresAllowed")
    self._turn_toggle = BigParamControl("slow for route turns", "NavTurnSlowdown")
    self._lane_toggle = BigParamControl("show lanes", "NavLaneGuidance")
    self._lane_timer = BigMultiParamToggle("route lane change delay", "NavLaneChangeTimer", ["off", "nudgeless", "0.5 s", "1 s", "2 s", "3 s"])
    self._quiet_toggle = BigParamControl("always show next turn", "NavMiciQuietGlyph")

    self._scroller.add_widgets([self._info, self._nav_toggle, self._end_route_btn, self._desires_toggle, self._turn_toggle,
                                self._lane_toggle, self._lane_timer, self._quiet_toggle])
    self._refresh_toggles = (("AllowNavigation", self._nav_toggle), ("NavDesiresAllowed", self._desires_toggle),
                             ("NavTurnSlowdown", self._turn_toggle), ("NavLaneGuidance", self._lane_toggle),
                             ("NavMiciQuietGlyph", self._quiet_toggle))

  def _refresh(self) -> None:
    ui_state.update_params()
    for key, item in self._refresh_toggles:
      item.set_checked(ui_state.params.get_bool(key))
    self._lane_timer._load_value()
    self._end_route_btn.set_visible(bool(ui_state.params.get("MapboxRoute")))
    if ui_state.CP is not None:
      self._turn_toggle.set_visible(ui_state.has_longitudinal_control)

  def _update_state(self):
    super()._update_state()
    self._nav_status.update()
    self._info.status_text.set_text(nav_status_line(self._nav_status.state, self._nav_status.online))
    self._info.destination_text.set_text(self._nav_status.destination or tr("not set"))
    self._frame += 1
    if self._frame % REFRESH_FRAMES == 0:
      self._refresh()

  def show_event(self):
    super().show_event()
    self._refresh()

  def _end_route(self) -> None:
    # navigationd drops the route once the destination param is empty; the log line attributes
    # the clear to a deliberate tap, distinguishing it from a param glitch
    cloudlog.warning("ui: destination cleared from the navigation page")
    ui_state.params.put("MapboxRoute", "")
    self._end_route_btn.set_visible(False)
