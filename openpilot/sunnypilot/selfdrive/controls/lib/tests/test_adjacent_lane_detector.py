"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from types import SimpleNamespace

from openpilot.cereal import log
from openpilot.sunnypilot.selfdrive.controls.lib.adjacent_lane_detector import AdjacentLaneDetector, CONFIRM_FRAMES
from openpilot.common.test import OpenpilotTestCase

LEFT = log.LaneChangeDirection.left
RIGHT = log.LaneChangeDirection.right

N_POINTS = 33
X = [i * 6.0 for i in range(N_POINTS)]


def _line(y):
  ys = [y] * N_POINTS if isinstance(y, float) else y
  return SimpleNamespace(x=X, y=ys)


# defaults are a measured real frame: a two-lane road with ~3.1m of oncoming
# lane on the left and 0.35m of shoulder on the right
def _model(lines=(-4.2, -1.3, 1.75, 2.9), probs=(0.9, 0.99, 0.98, 0.0),
           edges=(-4.4, 2.1), stds=(0.4, 0.24)):
  return SimpleNamespace(
    laneLines=[_line(y) for y in lines],
    laneLineProbs=list(probs),
    roadEdges=[_line(y) for y in edges],
    roadEdgeStds=list(stds),
  )


def _warm(detector, model, v_ego=30.0, frames=CONFIRM_FRAMES):
  for _ in range(frames):
    detector.update(model, v_ego)


class TestAdjacentLaneDetector(OpenpilotTestCase):
  def test_no_pavement_side_never_confirms(self):
    detector = AdjacentLaneDetector()
    _warm(detector, _model())
    assert detector.available(LEFT)
    assert not detector.available(RIGHT)

  def test_takes_the_full_window_to_confirm(self):
    detector = AdjacentLaneDetector()
    _warm(detector, _model(), frames=CONFIRM_FRAMES - 1)
    assert not detector.available(LEFT)
    detector.update(_model(), 30.0)
    assert detector.available(LEFT)

  def test_one_bad_frame_resets_the_window(self):
    detector = AdjacentLaneDetector()
    _warm(detector, _model())
    detector.update(_model(edges=(-3.0, 2.1)), 30.0)  # left edge pinches to 1.7m
    assert not detector.available(LEFT)
    _warm(detector, _model(), frames=CONFIRM_FRAMES - 1)
    assert not detector.available(LEFT)

  def test_uncertain_road_edge_blocks(self):
    detector = AdjacentLaneDetector()
    _warm(detector, _model(stds=(2.5, 0.24)))
    assert not detector.available(LEFT)

  def test_faded_lane_line_blocks(self):
    detector = AdjacentLaneDetector()
    _warm(detector, _model(probs=(0.9, 0.2, 0.98, 0.0)))
    assert not detector.available(LEFT)

  def test_lane_ending_ahead_blocks_only_within_lookahead(self):
    # the left edge pinches in at 60m: 3.1m of room until then, 1.2m after
    pinched = [-4.4 if x < 60.0 else -2.5 for x in X]
    model = _model(edges=(pinched, 2.1))
    fast = AdjacentLaneDetector()
    _warm(fast, model, v_ego=30.0)  # 90m lookahead reaches the pinch
    assert not fast.available(LEFT)
    slow = AdjacentLaneDetector()
    _warm(slow, model, v_ego=5.0)  # 30m lookahead does not
    assert slow.available(LEFT)

  def test_missing_model_outputs_read_unavailable(self):
    detector = AdjacentLaneDetector()
    _warm(detector, _model())
    detector.update(SimpleNamespace(laneLines=[], roadEdges=[]), 30.0)
    assert not detector.available(LEFT)
    assert not detector.available(RIGHT)
