"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.tools.nav_demo import LANE_SHOW_M, SCENARIOS, message_stream
from openpilot.common.test import OpenpilotTestCase


def run(names, speedup=1.0):
  """(states, messages, cue edges) for a full scripted run."""
  states, msgs, edges = [], [], []
  last_id = 0
  for _, state, msg in message_stream(names, speedup):
    states.append(state)
    msgs.append(msg)
    nav = msg.navigationd
    if nav.audioCueId != last_id:
      last_id = nav.audioCueId
      edges.append((nav.audioCueStage, nav.audioCueKind, nav.audioCueDirection))
  return states, msgs, edges


def labels(states):
  return [s.label for s in states if s.label]


class TestApproach(OpenpilotTestCase):
  def test_cues_come_from_the_real_windows(self):
    _, _, edges = run(['approach'])
    assert edges == [('approach', 'turn', 'right'), ('imminent', 'turn', 'right')]

  def test_labeled_moments_in_order(self):
    states, _, _ = run(['approach'])
    assert labels(states) == ['quiet', 'approach', 'lanes', 'turn', 'after_turn']

  def test_message_carries_the_upcoming_maneuver(self):
    states, msgs, _ = run(['approach'])
    nav = msgs[0].navigationd
    assert nav.valid
    assert msgs[0].valid
    # depart behind the car, then the three legs ahead
    assert len(nav.allManeuvers) == 4
    assert nav.allManeuvers[0].distance == 0.0
    assert nav.allManeuvers[1].type == 'turn'
    assert abs(nav.allManeuvers[1].distance - states[0].dist) < 1e-6
    assert nav.distanceRemaining == 800.0 + 600.0 + 1200.0

  def test_lanes_only_appear_on_the_close_approach(self):
    states, msgs, _ = run(['approach'])
    seen = False
    for state, msg in zip(states, msgs, strict=True):
      if len(msg.navigationd.lanes):
        seen = True
        assert state.dist < LANE_SHOW_M
        assert msg.navigationd.lanes[2].active
        assert msg.navigationd.lanes[2].activeDirection == 'right'
    assert seen

  def test_speedup_does_not_skip_cues(self):
    _, _, edges = run(['approach'], speedup=4.0)
    assert [e[0] for e in edges] == ['approach', 'imminent']


class TestReroute(OpenpilotTestCase):
  def test_exactly_one_reroute_cue(self):
    _, _, edges = run(['reroute'])
    assert edges == [('reroute', 'reroute', 'none')]

  def test_new_route_replaces_the_old(self):
    states, msgs, _ = run(['reroute'])
    idx = next(i for i, s in enumerate(states) if s.label == 'rerouted')
    assert msgs[idx].navigationd.allManeuvers[1].modifier == 'left'
    assert msgs[idx].navigationd.distanceFromRoute < 10.0
    off = next(i for i, s in enumerate(states) if s.label == 'off_route')
    assert msgs[off].navigationd.distanceFromRoute > 200.0

  def test_route_state_tracks_the_drift(self):
    states, msgs, _ = run(['reroute'])
    by_label = {s.label: m for s, m in zip(states, msgs, strict=True) if s.label}
    assert by_label['on_route'].navigationd.routeState == 'onRoute'
    assert by_label['off_route'].navigationd.routeState == 'offRoute'
    assert by_label['reroute'].navigationd.routeState == 'rerouting'
    assert by_label['rerouted'].navigationd.routeState == 'onRoute'


class TestArrival(OpenpilotTestCase):
  def test_one_arrive_cue_then_cleanup(self):
    states, msgs, edges = run(['arrival'])
    assert edges == [('arrive', 'arrive', 'none')]
    assert not msgs[-1].navigationd.valid
    assert len(msgs[-1].navigationd.allManeuvers) == 0
    assert states[-1].destination == ''

  def test_car_stops_at_the_flag(self):
    states, _, _ = run(['arrival'])
    driving = [s for s in states if s.route is not None]
    assert driving[-1].v < 1.0


class TestFailure(OpenpilotTestCase):
  def test_failures_publish_then_recovery(self):
    states, msgs, edges = run(['failure'])
    assert edges == []
    assert max(m.navigationd.routeFailures for m in msgs) == 2
    # invalid while failing, valid once a route lands
    assert not msgs[0].navigationd.valid
    assert msgs[-1].navigationd.valid
    # the scenario opens without a localizer fix (the pole stage of the searching flag)
    # and the fix holds from the moment it lands
    no_fix = next(i for i, s in enumerate(states) if s.label == 'no_fix')
    fix_in = next(i for i, s in enumerate(states) if s.gps_ok)
    assert not any(m.valid for m in msgs[:fix_in])
    assert no_fix < fix_in
    assert all(m.valid for m in msgs[fix_in:])


class TestTour(OpenpilotTestCase):
  def test_the_whole_tour_holds_together(self):
    states, msgs, _ = run(list(SCENARIOS))
    assert len(states) == len(msgs)
    ids = [m.navigationd.audioCueId for m in msgs]
    assert ids == sorted(ids)
    # every scenario contributed at least one labeled moment
    assert len(labels(states)) >= 10
