"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from types import SimpleNamespace

import numpy as np

from openpilot.sunnypilot.navd.nav_audio import NavAudioCues, maneuver_event
from openpilot.sunnypilot.navd.navigationd import Guidance
from openpilot.sunnypilot.selfdrive.ui import nav_sounds
from openpilot.sunnypilot.selfdrive.ui.nav_sounds import NavAudioPlayer, earcon_wave
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase

# at exactly the 22.4 m/s breakpoint the stage distances are 500 m (approach) and 130 m (imminent)
V_CRUISE = 22.4


def _progress(distance: float, mtype: str = 'turn', modifier: str = 'right', step_idx: int = 3,
              step_len: float = 2000.0, instruction: str = '') -> SimpleNamespace:
  return SimpleNamespace(
    current_step_idx=step_idx,
    distance_to_end_of_step=distance,
    current_step=SimpleNamespace(distance=step_len),
    next_turn=SimpleNamespace(maneuver=mtype, modifier=modifier, instruction=instruction),
  )


QUIET = Guidance()


class TestManeuverEvents(NavdTestCase):
  def test_turns(self):
    assert maneuver_event('turn', 'left') == ('turn', 'left', 0)
    assert maneuver_event('turn', 'right') == ('turn', 'right', 0)
    assert maneuver_event('turn', 'slightLeft') == ('slightTurn', 'left', 0)
    assert maneuver_event('turn', 'sharpRight') == ('sharpTurn', 'right', 0)
    assert maneuver_event('turn', 'uturn') == ('uturn', 'left', 0)
    assert maneuver_event('end of road', 'left') == ('turn', 'left', 0)

  def test_typed_maneuvers_carry_their_kind(self):
    assert maneuver_event('off ramp', 'slightRight') == ('exit', 'right', 0)
    assert maneuver_event('merge', 'slightLeft') == ('merge', 'left', 0)
    assert maneuver_event('fork', 'right') == ('keep', 'right', 0)

  def test_non_actions_are_silent(self):
    assert maneuver_event('continue', 'straight') is None
    assert maneuver_event('merge', 'straight') is None
    assert maneuver_event('depart', 'right') is None
    assert maneuver_event('arrive', 'none') is None

  def test_bends_and_name_changes_are_not_turns(self):
    assert maneuver_event('continue', 'left') is None
    assert maneuver_event('continue', 'slightRight') is None
    assert maneuver_event('new name', 'slightLeft') is None
    assert maneuver_event('new name', 'right') is None
    # a turn onto a road that also changes name still arrives as a turn
    assert maneuver_event('turn', 'left') == ('turn', 'left', 0)

  def test_a_continue_uturn_is_still_a_uturn(self):
    assert maneuver_event('continue', 'uturn') == ('uturn', 'left', 0)

  def test_roundabout_exit_number_comes_from_instruction(self):
    assert maneuver_event('roundabout', 'right', 'Enter the roundabout and take the 3rd exit') == ('roundabout', 'right', 3)
    assert maneuver_event('rotary', 'right') == ('roundabout', 'right', 0)
    assert maneuver_event('exit roundabout', 'right') is None

  def test_only_an_ordinal_reads_as_an_exit_number(self):
    assert maneuver_event('roundabout', 'right', 'At the roundabout, exit onto A40 exit') == ('roundabout', 'right', 0)
    assert maneuver_event('roundabout', 'right', 'Take the 1st exit onto A40') == ('roundabout', 'right', 1)
    assert maneuver_event('roundabout', 'right', 'Take the 12th exit') == ('roundabout', 'right', 9)


class TestStages(NavdTestCase):
  def test_two_stage_prompt_never_repeats(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(600.0), QUIET, V_CRUISE, False)
    assert cues.cue_id == 0
    cues.update(route, _progress(450.0), QUIET, V_CRUISE, False)
    assert (cues.kind, cues.stage, cues.cue_id) == ('turn', 'approach', 1)
    assert cues.direction == 'right'
    cues.update(route, _progress(440.0), QUIET, V_CRUISE, False)
    assert cues.cue_id == 1
    cues.update(route, _progress(100.0), QUIET, V_CRUISE, False)
    assert (cues.kind, cues.stage, cues.cue_id) == ('turn', 'imminent', 2)
    cues.update(route, _progress(60.0), QUIET, V_CRUISE, False)
    assert cues.cue_id == 2

  def test_chained_maneuvers_skip_the_approach(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(450.0, step_len=300.0), QUIET, V_CRUISE, False)
    assert cues.cue_id == 0
    cues.update(route, _progress(100.0, step_len=300.0), QUIET, V_CRUISE, False)
    assert (cues.stage, cues.cue_id) == ('imminent', 1)

  def test_crawling_defers_the_approach(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(100.0), QUIET, 2.0, False)
    assert cues.cue_id == 0
    cues.update(route, _progress(100.0), QUIET, 10.0, False)
    assert (cues.stage, cues.cue_id) == ('approach', 1)

  def test_route_change_resets_fired_state(self):
    cues = NavAudioCues()
    cues.update({}, _progress(450.0), QUIET, V_CRUISE, False)
    assert cues.cue_id == 1
    cues.update({}, _progress(450.0), QUIET, V_CRUISE, False)
    assert (cues.stage, cues.cue_id) == ('approach', 2)

  def test_no_progress_no_cue(self):
    cues = NavAudioCues()
    cues.update(None, None, QUIET, V_CRUISE, False)
    assert cues.cue_id == 0


class TestEventCues(NavdTestCase):
  def test_lane_cue_yields_to_the_stage_prompt(self):
    cues, route = NavAudioCues(), {}
    guidance = Guidance(lane_change_direction='left')
    cues.update(route, _progress(450.0), guidance, V_CRUISE, False)
    assert (cues.kind, cues.stage) == ('turn', 'approach')
    cues.update(route, _progress(440.0), guidance, V_CRUISE, False)
    assert (cues.kind, cues.direction, cues.stage, cues.cue_id) == ('laneChange', 'left', 'lane', 2)
    cues.update(route, _progress(430.0), guidance, V_CRUISE, False)
    assert cues.cue_id == 2

  def test_reroute_fires_once_per_excursion(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(5000.0, modifier='straight', mtype='continue'), QUIET, V_CRUISE, True)
    assert (cues.kind, cues.stage, cues.cue_id) == ('reroute', 'reroute', 1)
    cues.update(route, _progress(5000.0, modifier='straight', mtype='continue'), QUIET, V_CRUISE, True)
    assert cues.cue_id == 1
    cues.update(route, _progress(5000.0, modifier='straight', mtype='continue'), QUIET, V_CRUISE, False)
    cues.update(route, _progress(5000.0, modifier='straight', mtype='continue'), QUIET, V_CRUISE, True)
    assert (cues.stage, cues.cue_id) == ('reroute', 2)

  def test_arrival_fires_once(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(20.0, mtype='arrive', modifier='none'), Guidance(arrived=True), 0.5, False)
    assert (cues.kind, cues.stage, cues.cue_id) == ('arrive', 'arrive', 1)
    cues.update(route, _progress(20.0, mtype='arrive', modifier='none'), Guidance(arrived=True), 0.5, False)
    assert cues.cue_id == 1

  def test_a_far_maneuver_earns_no_cue(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(5000.0), QUIET, V_CRUISE, False)
    assert cues.cue_id == 0


ALL_KINDS = [('turn', 'left'), ('turn', 'right'), ('slightTurn', 'left'), ('slightTurn', 'right'),
             ('sharpTurn', 'left'), ('sharpTurn', 'right'), ('keep', 'left'), ('keep', 'right'),
             ('exit', 'left'), ('exit', 'right'), ('merge', 'left'), ('merge', 'right'),
             ('uturn', 'left'), ('roundabout', 'right'), ('laneChange', 'left'), ('laneChange', 'right'),
             ('reroute', 'none'), ('arrive', 'none')]


class TestSynthesis(NavdTestCase):
  def test_every_kind_renders(self):
    for kind, direction in ALL_KINDS:
      for stage in ('approach', 'imminent'):
        wave = earcon_wave(kind, stage, direction)
        assert wave.dtype == np.float32 and len(wave) > 0
        assert np.all(np.isfinite(wave)) and np.max(np.abs(wave)) <= nav_sounds.AMPLITUDE + 1e-6

  def test_direction_carries_the_tone_contour(self):
    # left and right are the same length but different contours
    right = earcon_wave('turn', 'approach', 'right')
    left = earcon_wave('turn', 'approach', 'left')
    assert len(right) == len(left)
    assert not np.array_equal(right, left)

  def test_envelopes_kill_clicks(self):
    wave = earcon_wave('turn', 'approach', 'right')
    assert abs(wave[0]) < 1e-3 and abs(wave[-1]) < 1e-3


class _FakeSM:
  def __init__(self, cue_id: int, kind: str = 'turn', stage: str = 'approach', direction: str = 'right', count: int = 0):
    self.updated = {'navigationd': True}
    self._msg = SimpleNamespace(audioCueId=cue_id, audioCueKind=kind, audioCueStage=stage,
                                audioCueDirection=direction, audioCueCount=count)

  def __getitem__(self, _):
    return self._msg


class TestPlayer(NavdTestCase):
  def _player(self, monkeypatch, enabled: bool = True) -> NavAudioPlayer:
    monkeypatch.setattr(NavAudioPlayer, '_read_params', lambda self: None)
    player = NavAudioPlayer()
    player.enabled = enabled
    return player

  def test_late_subscriber_swallows_the_sticky_cue(self, monkeypatch):
    player = self._player(monkeypatch)
    player.update(_FakeSM(7))
    assert not player.active
    player.update(_FakeSM(8))
    assert player.active

  def test_off_stays_silent(self, monkeypatch):
    player = self._player(monkeypatch, enabled=False)
    player.update(_FakeSM(1))
    player.update(_FakeSM(2))
    assert not player.active

  def test_an_unrenderable_kind_stays_silent(self, monkeypatch):
    # nothing in the current vocabulary raises, so force it: the guard is for cue kinds
    # from a newer navigationd than this build
    logged = []
    monkeypatch.setattr(nav_sounds.cloudlog, 'exception', lambda msg: logged.append(msg))
    real_earcon_wave = nav_sounds.earcon_wave

    def picky_earcon_wave(kind, *args, **kwargs):
      if kind == 'teleport':
        raise KeyError(kind)
      return real_earcon_wave(kind, *args, **kwargs)

    monkeypatch.setattr(nav_sounds, 'earcon_wave', picky_earcon_wave)
    player = self._player(monkeypatch)
    player.update(_FakeSM(1, kind='teleport'))
    player.update(_FakeSM(2, kind='teleport'))
    player.update(_FakeSM(3, kind='teleport'))
    assert not player.active
    assert len(logged) == 1
    # the next cue this build does understand still plays
    player.update(_FakeSM(4, kind='turn'))
    assert player.active

  def test_alert_cancel_drops_the_transmission(self, monkeypatch):
    player = self._player(monkeypatch)
    player.update(_FakeSM(1))
    player.update(_FakeSM(2))
    frames = player.get_frames(256)
    assert np.any(frames != 0)
    player.cancel()
    assert not player.active
    assert np.all(player.get_frames(256) == 0)
