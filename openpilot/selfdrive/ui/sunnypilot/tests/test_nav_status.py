"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.cereal import custom, log
from openpilot.common.params import Params

from openpilot.selfdrive.ui.sunnypilot import nav_status as nav_status_module
from openpilot.selfdrive.ui.sunnypilot.nav_status import (
  GPS_ACQUIRE_CONFIRM_SECONDS, GPS_LOST_HOLD_SECONDS, ROUTE_FAILURE_THRESHOLD, NavState, NavStatus,
)
from openpilot.common.test import Mocker, OpenpilotTestCase

NetworkType = log.DeviceState.NetworkType

STARTED_FRAME = 100


class MockSM(dict):
  def __init__(self):
    super().__init__()
    self.alive = {'navigationd': False}
    # navigationd publishes msg.valid straight from the localizer, and navigationd.valid once a route loads
    self.valid = {'navigationd': False}
    # fresher than started_frame: data received during this drive, not the previous one
    self.recv_frame = {'navigationd': STARTED_FRAME + 1}
    self['navigationd'] = custom.Navigationd.new_message()
    self.set_network(NetworkType.cell4G)

  def set(self, alive: bool, gps_valid: bool = False, route_valid: bool = False, failures: int = 0):
    self.alive['navigationd'] = alive
    self.valid['navigationd'] = gps_valid
    msg = custom.Navigationd.new_message()
    msg.valid = route_valid
    msg.routeFailures = failures
    self['navigationd'] = msg

  def set_network(self, network_type):
    msg = log.DeviceState.new_message()
    msg.networkType = network_type
    self['deviceState'] = msg


class TestNavStatus(OpenpilotTestCase):
  def setup_method(self):
    self.params = Params()
    self.params.put("MapboxRoute", "", block=True)
    self.params.put("AllowNavigation", True, block=True)

    self.sm = MockSM()
    self.now = 1000.0
    # module and singleton state, patched for the test and restored by the base's cleanup
    mocker = Mocker(self.addCleanup)
    mocker.patch.object(nav_status_module.ui_state, 'sm', self.sm)
    mocker.patch.object(nav_status_module.ui_state, 'started_frame', STARTED_FRAME)
    mocker.patch.object(nav_status_module, 'monotonic', lambda: self.now)

    self.status = NavStatus()

  def tick(self, seconds: float = 0.0):
    self.now += seconds
    # the destination is only re-read on a poll interval, so force it when the clock hasn't moved
    self.status._last_poll_time = 0.0
    self.status.update()

  def acquire_fix(self):
    self.tick()
    self.tick(GPS_ACQUIRE_CONFIRM_SECONDS)

  def set_destination(self, destination: str):
    self.params.put("MapboxRoute", destination, block=True)

  def test_offline_when_navd_is_not_publishing(self):
    self.sm.set(alive=False)
    self.tick()
    assert self.status.state == NavState.OFFLINE
    assert not self.status.gps_locked

  def test_no_destination(self):
    self.sm.set(alive=True, gps_valid=True)
    self.acquire_fix()
    assert self.status.state == NavState.NO_DESTINATION
    assert self.status.gps_locked

  def test_waiting_for_gps_with_a_destination(self):
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=False)
    self.tick()
    assert self.status.state == NavState.WAITING_FOR_GPS
    assert self.status.destination == "740 E Ventura Blvd"

  def test_computing_then_active(self):
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=True)
    self.acquire_fix()
    assert self.status.state == NavState.COMPUTING

    self.sm.set(alive=True, gps_valid=True, route_valid=True)
    self.tick()
    assert self.status.state == NavState.ACTIVE

  def test_gps_lock_is_held_across_a_dropped_sample(self):
    self.sm.set(alive=True, gps_valid=True)
    self.acquire_fix()
    assert self.status.gps_locked

    self.sm.set(alive=True, gps_valid=False)
    self.tick(GPS_LOST_HOLD_SECONDS / 2)
    assert self.status.gps_locked, "a single dropped localizer sample must not flicker the indicator"

    self.tick(GPS_LOST_HOLD_SECONDS)
    assert not self.status.gps_locked

  def test_stale_validity_does_not_survive_navd_dying(self):
    self.sm.set(alive=True, gps_valid=True)
    self.acquire_fix()
    assert self.status.gps_locked

    self.sm.alive['navigationd'] = False
    self.tick()
    assert not self.status.gps_locked
    assert self.status.state == NavState.OFFLINE

  def test_a_never_seen_fix_is_not_reported_as_locked(self):
    self.now = 0.5
    self.sm.set(alive=True, gps_valid=False)
    self.tick()
    assert not self.status.gps_locked

  def test_a_stale_lock_from_the_previous_drive_is_ignored(self):
    self.sm.set(alive=True, gps_valid=True)
    self.sm.recv_frame['navigationd'] = STARTED_FRAME - 1
    self.tick()
    assert not self.status.gps_locked
    assert self.status.state == NavState.OFFLINE

    # fresh data still has to earn the lock
    self.sm.recv_frame['navigationd'] = STARTED_FRAME + 1
    self.sm.set(alive=True, gps_valid=False)
    self.tick()
    assert not self.status.gps_locked

  def test_a_single_valid_sample_does_not_show_a_lock(self):
    self.sm.set(alive=True, gps_valid=True)
    self.tick()
    assert not self.status.gps_locked, "a fix must be sustained before it is believed"

    self.sm.set(alive=True, gps_valid=False)
    self.tick(0.1)
    assert not self.status.gps_locked

  def test_failures_are_reported_as_no_route(self):
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=True, failures=0)
    self.acquire_fix()
    assert self.status.state == NavState.COMPUTING, "no failures yet, so it is still 'trying'"

    # a single failure already means tens of seconds without a route, so it is shown
    self.sm.set(alive=True, gps_valid=True, failures=ROUTE_FAILURE_THRESHOLD)
    self.tick()
    assert self.status.state == NavState.NO_ROUTE
    assert self.status.destination == "740 E Ventura Blvd"

  def test_connectivity_is_tracked_alongside_failures(self):
    # no connection is the common cause of failing route requests and the one the driver
    # can act on, so the flag is kept current for whatever surfaces the failure
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=True, failures=5)

    self.sm.set_network(NetworkType.none)
    self.tick()
    assert self.status.state == NavState.NO_ROUTE
    assert not self.status.online

    self.sm.set_network(NetworkType.cell4G)
    self.tick()
    assert self.status.online

  def test_failures_outrank_a_dropped_fix(self):
    # navd keeps its last position, so it goes on requesting routes after the localizer drops;
    # reporting "waiting for GPS" there would hide the real failure
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=False, failures=5)
    self.tick()
    assert self.status.state == NavState.NO_ROUTE

  def test_a_loaded_route_outranks_stale_failures(self):
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=True, route_valid=True, failures=3)
    self.acquire_fix()
    assert self.status.state == NavState.ACTIVE

  def test_allow_navigation_is_tracked(self):
    self.params.put("AllowNavigation", False, block=True)
    self.sm.set(alive=True, gps_valid=True)
    self.tick()
    assert not self.status.allow_navigation

    self.params.put("AllowNavigation", True, block=True)
    self.tick()
    assert self.status.allow_navigation


class TestNavStatusLine(OpenpilotTestCase):
  """The settings panel's status line; it shares NavStatus with the chip, so the mapping
  from state to words is the only logic it owns."""

  def test_every_state_has_a_line(self):
    from openpilot.selfdrive.ui.sunnypilot.layouts.settings.navigation import NAV_STATUS_TEXTS, nav_status_line
    for state in NavState:
      assert nav_status_line(state, online=True) == NAV_STATUS_TEXTS[state]

  def test_offline_outranks_only_the_blocked_states(self):
    from openpilot.selfdrive.ui.sunnypilot.layouts.settings.navigation import (
      NAV_STATUS_OFFLINE, NAV_STATUS_TEXTS, nav_status_line)
    # a route request is what needs the network; a GPS wait or an active route reads the same
    assert nav_status_line(NavState.COMPUTING, online=False) == NAV_STATUS_OFFLINE
    assert nav_status_line(NavState.NO_ROUTE, online=False) == NAV_STATUS_OFFLINE
    for state in (NavState.OFFLINE, NavState.NO_DESTINATION, NavState.WAITING_FOR_GPS, NavState.ACTIVE):
      assert nav_status_line(state, online=False) == NAV_STATUS_TEXTS[state]
