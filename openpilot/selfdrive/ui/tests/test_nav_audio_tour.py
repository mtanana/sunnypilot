"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.selfdrive.ui.sunnypilot.nav_audio_tour import STEPS
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import ARROW_ANGLES
from openpilot.sunnypilot.selfdrive.ui.nav_sounds import earcon_wave
from openpilot.common.test import OpenpilotTestCase


class TestTourSteps(OpenpilotTestCase):
  def test_every_step_renders(self):
    for step in STEPS:
      wave = earcon_wave(step.kind, step.stage, step.direction)
      assert len(wave) > 0 and np.all(np.isfinite(wave)), step.kind

  def test_cards_use_real_modifiers(self):
    for step in STEPS:
      if step.card is not None:
        _, modifier, _ = step.card
        assert modifier in ARROW_ANGLES or modifier == 'uturn', step.kind

  def test_tour_teaches_every_sound(self):
    assert {step.kind for step in STEPS} == {'turn', 'laneChange', 'reroute', 'arrive'}
    assert {step.direction for step in STEPS} >= {'left', 'right'}
    assert {step.stage for step in STEPS} >= {'approach', 'imminent'}
