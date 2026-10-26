"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from typing import cast

import openpilot.cereal.messaging as messaging
from openpilot.cereal import custom
from openpilot.common.params import Params

from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.navd.constants import BannerMode, NAV_BANNER
from openpilot.sunnypilot.navd.event_builder import EventBuilder
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase


class _MockSM(dict):
  def __init__(self, nav_msg):
    super().__init__()
    self['navigationd'] = nav_msg


def MockSM(nav_msg) -> messaging.SubMaster:
  # the builder only ever indexes the SubMaster, so a dict stands in for one
  return cast(messaging.SubMaster, _MockSM(nav_msg))


class TestEventBuilder(NavdTestCase):
  def setup_method(self):
    self.params = Params()
    self.event_builder = EventBuilder()

  def create_nav_msg(self, upcoming_turn='none', valid=True):
    nav_msg = custom.Navigationd.new_message()
    nav_msg.valid = valid
    nav_msg.upcomingTurn = upcoming_turn
    nav_msg.allManeuvers = [
      custom.Navigationd.Maneuver.new_message(distance=192.84873284, type='turn', modifier='left', instruction='West Esplanade Drive'),
      custom.Navigationd.Maneuver.new_message(distance=192.84809314, type='turn', modifier='right', instruction='West Esplanade Drive'),
    ]
    return nav_msg

  def test_validity(self):
    nav_msg = self.create_nav_msg(valid=False)
    events = EventBuilder.build_navigation_events(MockSM(nav_msg))
    assert events == []

  def test_enabled(self):
    self.params.put("NavBannerMode", BannerMode.ALWAYS, block=True)
    nav_msg = self.create_nav_msg()
    events = self.event_builder.update(MockSM(nav_msg))
    expected = [{
      'name': custom.OnroadEventSP.EventName.navigationBanner,
      'message': 'For 192m, Continue on West Esplanade Drive'
    }]
    assert events == expected

    self.params.put("NavBannerMode", BannerMode.OFF, block=True)
    self.event_builder._counter = 59
    events = self.event_builder.update(MockSM(nav_msg))
    assert events == []


  def test_build_navigation_events(self):
    nav_msg = self.create_nav_msg()
    events = EventBuilder.build_navigation_events(MockSM(nav_msg), False)
    expected = [{
      'name': custom.OnroadEventSP.EventName.navigationBanner,
      'message': 'For 650ft, Continue on West Esplanade Drive',
    }]
    assert events == expected

  def test_distance_condition_imperial(self):
    nav_msg = self.create_nav_msg()
    nav_msg.allManeuvers[1] = custom.Navigationd.Maneuver.new_message(distance=160.0, type='continue', modifier='straight', instruction='1234 Apple Way')
    events = EventBuilder.build_navigation_events(MockSM(nav_msg), False)
    expected = [{
      'name': custom.OnroadEventSP.EventName.navigationBanner,
      'message': 'For 500ft, Continue on 1234 Apple Way',
    }]
    assert events == expected

  def test_upcoming_turn_override(self):
    nav_msg = self.create_nav_msg(upcoming_turn='left')
    events = EventBuilder.build_navigation_events(MockSM(nav_msg))
    expected = [{
      'name': custom.OnroadEventSP.EventName.navigationBanner,
      'message': 'Turning Left, Make sure to nudge the wheel',
    }]
    assert events == expected

  def test_arrival_far_away_keeps_its_distance(self):
    nav_msg = self.create_nav_msg()
    nav_msg.allManeuvers[1] = custom.Navigationd.Maneuver.new_message(distance=1500.0, type='arrive', modifier='right',
                                                                      instruction='Your destination is on the right')
    events = EventBuilder.build_navigation_events(MockSM(nav_msg))
    assert events[0]['message'] == 'In 1.5 km, Your destination is on the right'

  def test_arrival_close_by_shows_the_raw_instruction(self):
    nav_msg = self.create_nav_msg()
    nav_msg.allManeuvers[1] = custom.Navigationd.Maneuver.new_message(distance=80.0, type='arrive', modifier='right',
                                                                      instruction='Your destination is on the right')
    events = EventBuilder.build_navigation_events(MockSM(nav_msg))
    assert events[0]['message'] == 'Your destination is on the right'

  def test_bear_is_a_maneuver_not_a_road_name(self):
    nav_msg = self.create_nav_msg()
    nav_msg.allManeuvers[1] = custom.Navigationd.Maneuver.new_message(distance=45.0, type='fork', modifier='slightLeft',
                                                                      instruction='Bear left onto Fairview Drive')
    events = EventBuilder.build_navigation_events(MockSM(nav_msg))
    assert events[0]['message'] == 'In 45m, Bear left onto Fairview Drive'

  def test_lane_hint_keeps_the_turn_text(self):
    # the HUD breathes the maneuver's glyph for a hint; the strip keeps saying where the turn is
    nav_msg = self.create_nav_msg()
    nav_msg.allManeuvers[1] = custom.Navigationd.Maneuver.new_message(distance=600.0, type='off ramp', modifier='slightRight', instruction='Exit 179')
    plain = EventBuilder.build_navigation_events(MockSM(nav_msg))[0]['message']
    nav_msg.laneChangeDirection = 'right'
    nav_msg.laneChangeAutoConfirm = True
    assert EventBuilder.build_navigation_events(MockSM(nav_msg))[0]['message'] == plain

  def test_straight(self):
    nav_msg = self.create_nav_msg()
    nav_msg.allManeuvers[1] = custom.Navigationd.Maneuver.new_message(distance=80.0, type='continue', modifier='straight', instruction='1234 Apple Way')

    events = EventBuilder.build_navigation_events(MockSM(nav_msg))
    expected = [{
      'name': custom.OnroadEventSP.EventName.navigationBanner,
      'message': 'For 80m, Continue on 1234 Apple Way'
    }]
    assert events == expected


BANNER = custom.OnroadEventSP.EventName.navigationBanner


class TestIncrementalBanners(NavdTestCase):
  def setup_method(self):
    self.params = Params()
    self.params.put("NavBannerMode", BannerMode.INCREMENTAL, block=True)
    self.event_builder = EventBuilder()
    self.event_builder._mode = BannerMode.INCREMENTAL

  def msg_at(self, distance, instruction='West Esplanade Drive'):
    nav_msg = custom.Navigationd.new_message()
    nav_msg.valid = True
    nav_msg.upcomingTurn = 'none'
    # build_navigation_events reads maneuver [1] when there is more than one
    nav_msg.allManeuvers = [
      custom.Navigationd.Maneuver.new_message(distance=distance, type='turn', modifier='left', instruction=instruction),
      custom.Navigationd.Maneuver.new_message(distance=distance, type='turn', modifier='left', instruction=instruction),
    ]
    return MockSM(nav_msg)

  def banners_while_approaching(self, distance, frames=1):
    n = 0
    for _ in range(frames):
      self.event_builder._counter = 1  # don't let the periodic param re-read fight the fixture
      n += sum(1 for e in self.event_builder.update(self.msg_at(distance)) if e['name'] == BANNER)
    return n

  def test_no_banner_before_the_first_milestone(self):
    assert self.banners_while_approaching(5000.0, frames=20) == 0

  def test_crossing_a_milestone_fires_a_burst_then_stops(self):
    fired = self.banners_while_approaching(2900.0, frames=200)
    assert fired == int(NAV_BANNER.SHOW_SECONDS / DT_MDL), "one bounded burst per milestone"
    # holding the same distance must not re-fire
    assert self.banners_while_approaching(2900.0, frames=50) == 0

  def test_each_milestone_fires_once(self):
    for d in (2900.0, 1400.0, 700.0, 300.0, 100.0, 40.0):
      assert self.banners_while_approaching(d, frames=200) > 0, f"expected a prompt crossing {d}m"
      assert self.banners_while_approaching(d, frames=20) == 0, f"{d}m re-fired without a new milestone"

  def test_several_milestones_at_once_still_fire_once(self):
    # a maneuver that appears already close shouldn't dump every prompt at the driver
    fired = self.banners_while_approaching(40.0, frames=400)
    assert fired == int(NAV_BANNER.SHOW_SECONDS / DT_MDL)

  def test_a_new_maneuver_restarts_the_sequence(self):
    assert self.banners_while_approaching(300.0, frames=200) > 0
    assert self.banners_while_approaching(300.0, frames=20) == 0

    # next turn: distance jumps back up, so the milestones start again
    n = 0
    for _ in range(200):
      self.event_builder._counter = 1
      n += sum(1 for e in self.event_builder.update(self.msg_at(2900.0, 'Oak Street')) if e['name'] == BANNER)
    assert n > 0, "a new maneuver must prompt again"

  def test_always_mode_holds_the_banner(self):
    self.event_builder._mode = BannerMode.ALWAYS
    assert self.banners_while_approaching(5000.0, frames=20) == 20
