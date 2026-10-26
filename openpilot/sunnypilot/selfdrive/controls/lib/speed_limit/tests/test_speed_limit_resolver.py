"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import random
import time

from openpilot.common.parameterized import parameterized

import openpilot.cereal.messaging as messaging
from openpilot.cereal import custom
from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import LIMIT_MAX_MAP_DATA_AGE

from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver import SpeedLimitResolver, ALL_SOURCES
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Policy
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.tests.fake_submaster import FakeSubMaster

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source


# real capnp messages through the shared fake, so a field or attribute the resolver
# reads that a real SubMaster would not serve fails here instead of onroad
def setup_sm(nav_limit_kph: int = 0, nav_valid: bool = True) -> FakeSubMaster:
  car_state_sp = messaging.new_message('carStateSP')
  car_state_sp.carStateSP.speedLimit = random.uniform(0, 120)
  live_map = messaging.new_message('liveMapDataSP')
  live_map.liveMapDataSP.speedLimit = random.uniform(0, 120)
  live_map.liveMapDataSP.speedLimitValid = True
  gps = messaging.new_message('gpsLocation')
  gps.gpsLocation.unixTimestampMillis = int(time.monotonic() * 1e3)
  nav = messaging.new_message('navigationd')
  nav.navigationd.valid = nav_valid
  nav.navigationd.currentSpeedLimit = round(nav_limit_kph)
  return FakeSubMaster({'carStateSP': car_state_sp, 'liveMapDataSP': live_map,
                        'gpsLocation': gps, 'navigationd': nav})


parametrized_policies = parameterized.expand(
  [
    (Policy.car_state_only, 'carStateSP', SpeedLimitSource.car),
    (Policy.car_state_priority, 'carStateSP', SpeedLimitSource.car),
    (Policy.map_data_only, 'liveMapDataSP', SpeedLimitSource.map),
    (Policy.map_data_priority, 'liveMapDataSP', SpeedLimitSource.map),
  ],
  names=["policy", "sm_key", "function_key"]
)


def resolver_class():
  return SpeedLimitResolver


class TestSpeedLimitResolverValidation(OpenpilotTestCase):

  @parameterized.expand(list(Policy), names=["policy"])
  def test_initial_state(self, resolver_class, policy):
    resolver = resolver_class()
    resolver.policy = policy
    for source in ALL_SOURCES:
      if source in resolver.limit_solutions:
        assert resolver.limit_solutions[source] == 0.
        assert resolver.distance_solutions[source] == 0.

  @parametrized_policies
  def test_resolver(self, resolver_class, policy, sm_key, function_key):
    resolver = resolver_class()
    resolver.policy = policy
    sm = setup_sm()
    source_speed_limit = sm[sm_key].speedLimit

    # Assert the resolver
    resolver.update(source_speed_limit, sm)
    assert resolver.speed_limit == source_speed_limit
    assert resolver.source == ALL_SOURCES[function_key]

  def test_resolver_combined(self, resolver_class):
    resolver = resolver_class()
    resolver.policy = Policy.combined
    sm = setup_sm()
    socket_to_source = {'carStateSP': SpeedLimitSource.car, 'liveMapDataSP': SpeedLimitSource.map}
    minimum_key, minimum_speed_limit = min(
      ((key, sm[key].speedLimit) for key in
       socket_to_source.keys()), key=lambda x: x[1])

    # Assert the resolver
    resolver.update(minimum_speed_limit, sm)
    assert resolver.speed_limit == minimum_speed_limit
    assert resolver.source == socket_to_source[minimum_key]

  @parametrized_policies
  def test_parser(self, resolver_class, policy, sm_key, function_key):
    resolver = resolver_class()
    resolver.policy = policy
    sm = setup_sm()
    source_speed_limit = sm[sm_key].speedLimit

    # Assert the parsing
    resolver.update(source_speed_limit, sm)
    assert resolver.limit_solutions[ALL_SOURCES[function_key]] == source_speed_limit
    assert resolver.distance_solutions[ALL_SOURCES[function_key]] == 0.

  @parameterized.expand(list(Policy), names=["policy"])
  def test_resolve_interaction_in_update(self, resolver_class, policy):
    v_ego = 50
    resolver = resolver_class()
    resolver.policy = policy

    sm = setup_sm()
    resolver.update(v_ego, sm)

    # After resolution
    assert resolver.speed_limit is not None
    assert resolver.distance is not None
    assert resolver.source is not None

  @parameterized.expand(list(Policy), names=["policy"])
  def test_nav_fallback_when_policy_sources_empty(self, resolver_class, policy):
    resolver = resolver_class()
    resolver.policy = policy
    sm = setup_sm(nav_limit_kph=50)
    sm['carStateSP'].speedLimit = 0.
    sm['liveMapDataSP'].speedLimitValid = False

    resolver.update(10., sm)
    assert resolver.source == SpeedLimitSource.nav
    assert abs(resolver.speed_limit - 50 * CV.KPH_TO_MS) < 1e-6

  @parametrized_policies
  def test_nav_never_preempts_policy_sources(self, resolver_class, policy, sm_key, function_key):
    resolver = resolver_class()
    resolver.policy = policy
    sm = setup_sm(nav_limit_kph=50)
    source_speed_limit = sm[sm_key].speedLimit

    resolver.update(source_speed_limit, sm)
    assert resolver.source == ALL_SOURCES[function_key]
    assert resolver.speed_limit == source_speed_limit

  def test_stale_nav_ignored(self, resolver_class):
    resolver = resolver_class()
    sm = setup_sm(nav_limit_kph=50)
    sm.recv_time['navigationd'] = time.monotonic() - 2 * LIMIT_MAX_MAP_DATA_AGE
    resolver._get_from_nav(sm)
    assert resolver.limit_solutions[SpeedLimitSource.nav] == 0.

  def test_invalid_nav_ignored(self, resolver_class):
    resolver = resolver_class()
    sm = setup_sm(nav_limit_kph=50, nav_valid=False)
    resolver._get_from_nav(sm)
    assert resolver.limit_solutions[SpeedLimitSource.nav] == 0.

  @parameterized.expand(list(Policy), names=["policy"])
  def test_old_map_data_ignored(self, resolver_class, policy):
    resolver = resolver_class()
    resolver.policy = policy
    sm = setup_sm()
    sm['gpsLocation'].unixTimestampMillis = int((time.monotonic() - 2 * LIMIT_MAX_MAP_DATA_AGE) * 1e3)
    resolver._get_from_map_data(sm)
    assert resolver.limit_solutions[SpeedLimitSource.map] == 0.
    assert resolver.distance_solutions[SpeedLimitSource.map] == 0.


class TestResolverRealSubMaster(OpenpilotTestCase):
  """End to end on real objects: real pub sockets, a real SubMaster, real capnp messages.

  A faked SubMaster cannot catch an attribute the real one does not have; sm.rcv_time
  crashed plannerd onroad (2026-08-15) while the mocked suite stayed green. The nav
  branches drive through the real receive path here, with nothing faked."""

  SERVICES = ['carStateSP', 'liveMapDataSP', 'gpsLocation', 'navigationd']

  def setUp(self):
    super().setUp()
    self.pm = messaging.PubMaster(self.SERVICES)
    self.sm = messaging.SubMaster(self.SERVICES)
    self.resolver = SpeedLimitResolver()
    self.resolver.policy = Policy.car_state_priority

  def publish_nav(self, limit_kph: int, valid: bool = True) -> None:
    # messages the loop never publishes stay at their schema defaults, which is the
    # empty-sources case the nav fallback is defined against
    for _ in range(20):
      msg = messaging.new_message('navigationd')
      msg.valid = True
      msg.navigationd.valid = valid
      msg.navigationd.currentSpeedLimit = limit_kph
      self.pm.send('navigationd', msg)
      self.sm.update(100)
      if self.sm.updated['navigationd']:
        return
    raise AssertionError("navigationd message never arrived over the real socket")

  def test_nav_limit_present(self):
    self.publish_nav(50)
    self.resolver.update(10., self.sm)
    assert self.resolver.source == SpeedLimitSource.nav
    assert abs(self.resolver.speed_limit - 50 * CV.KPH_TO_MS) < 1e-6

  def test_nav_limit_absent(self):
    self.publish_nav(0)
    self.resolver.update(10., self.sm)
    assert self.resolver.source == SpeedLimitSource.none
    assert self.resolver.speed_limit == 0.

  def test_nav_limit_stale(self):
    self.publish_nav(50)
    # age the real receive stamp instead of sleeping out the staleness window
    self.sm.recv_time['navigationd'] -= LIMIT_MAX_MAP_DATA_AGE + 1.
    self.resolver.update(10., self.sm)
    assert self.resolver.source == SpeedLimitSource.none
    assert self.resolver.speed_limit == 0.
