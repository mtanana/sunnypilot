"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.cereal import log

LaneChangeDirection = log.LaneChangeDirection

# the narrowest strip of pavement between our lane line and the road edge that still
# counts as a lane; shoulders and gutters come in well under this
MIN_LANE_WIDTH = 2.5  # m
# the model's confidence in its own road edge estimate; beyond this the edge position is
# too soft to prove a lane exists
MAX_EDGE_STD = 1.0  # m
MIN_LINE_PROB = 0.5
# the check must hold over the stretch the lane change actually happens on
LOOKAHEAD_MIN = 30.0  # m
LOOKAHEAD_TIME = 3.0  # s
# frames a side must test clear before it reads available; one failing frame resets it
CONFIRM_FRAMES = 10

# per side: our lane line, the road edge beyond it, and which way "outward" points
SIDES = {
  LaneChangeDirection.left: (1, 0, -1.0),
  LaneChangeDirection.right: (2, 1, 1.0),
}


# Answers "does the model see a drivable lane next to us on that side?" so a navigation
# hint can never confirm a lane change toward a road edge. Deliberately one-sided: a False
# only withholds the nav auto-confirmation, the driver's own nudge is never gated by this.
class AdjacentLaneDetector:
  def __init__(self):
    self._counters = {LaneChangeDirection.left: 0, LaneChangeDirection.right: 0}

  def reset(self) -> None:
    self._counters = dict.fromkeys(self._counters, 0)

  def update(self, model_data, v_ego: float) -> None:
    if len(model_data.laneLines) < 4 or len(model_data.roadEdges) < 2:
      self.reset()
      return

    lookahead = max(LOOKAHEAD_MIN, v_ego * LOOKAHEAD_TIME)
    window = np.array(model_data.laneLines[0].x) <= lookahead

    for direction, (line_idx, edge_idx, outward) in SIDES.items():
      line = model_data.laneLines[line_idx]
      width = outward * (np.array(model_data.roadEdges[edge_idx].y) - np.array(line.y))
      clear = (model_data.laneLineProbs[line_idx] >= MIN_LINE_PROB and
               model_data.roadEdgeStds[edge_idx] <= MAX_EDGE_STD and
               float(np.min(width[window])) >= MIN_LANE_WIDTH)
      self._counters[direction] = self._counters[direction] + 1 if clear else 0

  def available(self, direction) -> bool:
    return self._counters.get(direction, 0) >= CONFIRM_FRAMES
