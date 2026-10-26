"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from opendbc.car.structs import car
from openpilot.cereal import custom
from openpilot.selfdrive.selfdrived.events import ET
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase
from openpilot.sunnypilot.selfdrive.selfdrived.events import EVENTS_SP

EventNameSP = custom.OnroadEventSP.EventName

# navigation is opt-in and depends on the network and a third-party API, so nothing it does may
# gate engaging openpilot. Every escape hatch it relies on is pinned here.
NAV_EVENTS = [
  EventNameSP.navigationBanner,
]
# the only event types a navigation event may define: warnings and permanent text
NON_BLOCKING = {ET.WARNING, ET.PERMANENT}


# a fixture by name: the test base resolves a test method's parameters against this module
def selfdrived():
  return SelfdriveD(car.CarParams.new_message(), custom.CarParamsSP.new_message())


class TestEngagementIsolation(NavdTestCase):
  def test_navd_crash_does_not_raise_process_not_running(self, selfdrived):
    # navigationd is only_onroad, so manager marks it shouldBeRunning and a crash lands it in
    # not_running; processNotRunning is NO_ENTRY + SOFT_DISABLE
    assert 'navigationd' in selfdrived.ignored_processes

  def test_navd_silence_does_not_raise_comm_issue(self, selfdrived):
    # commIssue comes from all_alive/all_freq_ok/all_checks, which honor these ignore lists
    sm = selfdrived.sm
    assert 'navigationd' in sm.services, "navigationd must be subscribed for the checks to matter"
    for ignore_list in (sm.ignore_alive, sm.ignore_average_freq, sm.ignore_valid):
      assert 'navigationd' in ignore_list

    sm.alive['navigationd'] = False
    sm.valid['navigationd'] = False
    sm.freq_ok['navigationd'] = False
    # scoped to navigationd: nothing is publishing in a test, so the unscoped checks are all False
    assert sm.all_alive(['navigationd']), "a dead navigationd must not fail the alive check"
    assert sm.all_freq_ok(['navigationd'])
    assert sm.all_valid(['navigationd'])

  def test_nav_events_are_display_only(self):
    for event in NAV_EVENTS:
      with self.subTest(event=event):
        assert set(EVENTS_SP[event]) <= NON_BLOCKING, f"{event} defines {set(EVENTS_SP[event]) - NON_BLOCKING}"
