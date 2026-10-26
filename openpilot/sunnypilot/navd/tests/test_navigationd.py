"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import platform
from types import SimpleNamespace

import openpilot.cereal.messaging as messaging
from openpilot.common.test import Mocker

from openpilot.sunnypilot.navd.navigationd import OFF_ROUTE_DEBOUNCE_TICKS, Guidance, Navigationd
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase


class TestNavigationd(NavdTestCase):
  is_darwin = platform.system() == "Darwin"

  def setUp(self):
    super().setUp()
    self.mocker = Mocker(self.addCleanup)
    if self.is_darwin:
      self.mocker.patch('openpilot.cereal.messaging.SubMaster')
      self.mocker.patch('openpilot.cereal.messaging.PubMaster')

  def test_update_params(self):
    nav = Navigationd()
    nav.last_position = None
    nav._update_params()
    assert nav.frame == -1
    nav.last_position = Coordinate(latitude=37.0, longitude=128.0)
    nav._update_params()
    assert nav.frame == 0  # frame only updates when last position is set

  def test_update_navigation_no_position(self):
    nav = Navigationd()
    nav.last_position = None
    banner, progress, guidance = nav._update_navigation()
    assert banner == ''
    assert progress is None
    assert guidance == Guidance()

  def test_update_navigation(self):
    nav = Navigationd()
    nav.last_position = Coordinate(latitude=37.0, longitude=128.0)
    self.mocker.patch.object(nav, 'route', SimpleNamespace(route_id=1))
    banner, progress, guidance = nav._update_navigation()
    assert banner == ''
    assert progress is None  # navigation is not allowed, so the route is not consulted
    assert guidance == Guidance()

  def test_final_step_blocks_reroute(self):
    nav = Navigationd()
    nav.last_position = Coordinate(latitude=37.0, longitude=128.0)
    self.mocker.patch.object(nav.mapbox, 'set_destination', return_value=({}, False))
    nav.frame = 0  # keep clear of the periodic param re-read so the fixture state holds
    self.mocker.patch.object(nav, 'route', SimpleNamespace(route_id=1, steps=[]))
    nav.destination = "123 Main St"
    nav.new_destination = "123 Main St"
    nav.retry.attempted_destination = "123 Main St"
    nav.recompute_allowed = True
    nav.reroute_counter = 10

    nav.final_step = True
    nav._update_params()
    assert not nav.rerouting, "final step must not trigger a reroute"
    assert nav.route_request is None

    nav.final_step = False
    nav._update_params()
    assert nav.rerouting

  def test_drop_route_clears_final_step(self):
    nav = Navigationd()
    nav.final_step = True
    nav._drop_route()
    assert not nav.final_step

  def test_route_state_debounce_ignores_a_blip(self):
    nav = Navigationd()
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS - 1):
      nav._update_route_standing(True, False, False)
      assert not nav.off_route, "a blip against the thresholds must not dim the display"
    nav._update_route_standing(True, False, False)
    assert nav.off_route

  def test_route_state_exits_via_the_counter_reset(self):
    nav = Navigationd()
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS):
      nav._update_route_standing(True, False, False)
    assert nav.off_route
    nav._update_route_standing(False, False, False)
    assert not nav.off_route
    assert nav.reroute_counter == 0

  def test_bearing_misalign_counts_toward_off_route(self):
    nav = Navigationd()
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS):
      nav._update_route_standing(False, True, False)
    assert nav.off_route

  def test_recompute_off_parks_at_off_route(self):
    # with recompute off there is no reroute to hope for, but the display must still say
    # the route is not being followed; the reroute trigger itself stays gated elsewhere
    nav = Navigationd()
    nav.recompute_allowed = False
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS):
      nav._update_route_standing(True, False, False)
    assert nav.off_route
    assert nav.reroute_counter == OFF_ROUTE_DEBOUNCE_TICKS

  def test_arrival_hold_reports_on_route(self):
    # the last meters to the flag routinely leave the mapped line, and that is not lost
    nav = Navigationd()
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS):
      nav._update_route_standing(True, False, False)
    assert nav.off_route
    nav._update_route_standing(True, False, True)
    assert not nav.off_route

  def test_route_state_publishes_with_rerouting_outranking(self):
    nav = Navigationd()
    assert nav._build_navigation_message('', None, Guidance(), True).navigationd.routeState == 'onRoute'
    nav.off_route = True
    assert nav._build_navigation_message('', None, Guidance(), True).navigationd.routeState == 'offRoute'
    nav.rerouting = True
    assert nav._build_navigation_message('', None, Guidance(), True).navigationd.routeState == 'rerouting'

  def test_position_and_route_id_publish(self):
    nav = Navigationd()
    msg = nav._build_navigation_message('', None, Guidance(), True)
    assert msg.navigationd.hasPosition is False
    assert msg.navigationd.routeId == 0

    nav.last_position = Coordinate(latitude=34.226, longitude=-119.032)
    nav.last_bearing = -90.0
    self.mocker.patch.object(nav, 'route', SimpleNamespace(route_id=7))
    msg = nav._build_navigation_message('', None, Guidance(), True)
    assert msg.navigationd.hasPosition is True
    assert msg.navigationd.positionLatitude == 34.226
    assert msg.navigationd.positionLongitude == -119.032
    assert msg.navigationd.positionBearingDeg == 270.0  # published normalized to [0, 360)
    assert msg.navigationd.routeId == 7

  def test_build_navigation_message(self):
    if self.is_darwin:
      nav = Navigationd()
      msg = nav._build_navigation_message('', None, Guidance(), True)
      assert msg.navigationd.bannerInstructions == ''
      assert msg.navigationd.valid is False
    else:
      sm = messaging.SubMaster(['navigationd'])
      nav = Navigationd()
      msg = nav._build_navigation_message('', None, Guidance(), True)

      nav.pm.send('navigationd', msg)
      sm.update()
      received_msg = sm['navigationd']

      assert received_msg.bannerInstructions == msg.navigationd.bannerInstructions
      assert received_msg.valid == msg.navigationd.valid


class TestTurnTargetPublish(NavdTestCase):
  """The route's turns ahead reach the memory params for the longitudinal planner, and leave it
  with the route, off the route, while rerouting, and once passed."""

  def setUp(self):
    super().setUp()
    from openpilot.sunnypilot.navd.tests.test_turn_targets import ORIGIN, corner_route, offset
    self.nav = Navigationd()
    self.nav.allow_navigation = True
    self.nav.route = corner_route(0.0)
    self.start = ORIGIN
    self.past_corner = offset(ORIGIN, 30.0, 200.0)

  def publish(self, position: Coordinate) -> list | None:
    self.nav.last_position = position
    _, progress, _ = self.nav._update_navigation()
    self.nav._publish_turn_targets(progress)
    encoded = self.nav.mem_params.get("NavTargetVelocities")
    return json.loads(encoded) if encoded else None

  def test_turn_ahead_is_published(self):
    assert self.nav.route is not None
    corner = self.nav.route.steps[1].location
    assert self.publish(self.start) == [{'latitude': corner.latitude, 'longitude': corner.longitude, 'velocity': 6.5}]

  def test_passed_turn_leaves_the_list(self):
    assert self.publish(self.start) is not None
    assert self.publish(self.past_corner) is None

  def test_off_route_and_rerouting_clear(self):
    assert self.publish(self.start) is not None
    assert self.nav.route is not None
    self.nav.off_route = True
    self.nav._publish_turn_targets(self.nav.route.progress(self.start))
    assert self.nav.mem_params.get("NavTargetVelocities") is None
    self.nav.off_route = False
    self.nav.rerouting = True
    self.nav._publish_turn_targets(self.nav.route.progress(self.start))
    assert self.nav.mem_params.get("NavTargetVelocities") is None

  def test_route_loss_clears(self):
    assert self.publish(self.start) is not None
    self.nav.route = None
    assert self.publish(self.start) is None

  def test_car_scale(self):
    self.nav.turn_speed_scale = 1.25
    published = self.publish(self.start)
    assert published is not None
    self.assertAlmostEqual(published[0]['velocity'], 6.5 * 1.25)
