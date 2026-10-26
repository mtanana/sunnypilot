"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

When a navigation cue is owed and what it means (kind, side, count); how it sounds is
soundd's. A maneuver that needs no action gets no cue, no cue plays twice, and stages other
than imminent/reroute/arrive hold off below crawl speed.
"""
import re

from numpy import interp

from openpilot.sunnypilot.navd.helpers import ROUNDABOUT_TYPES

# meters to the maneuver at which each stage fires, against m/s; imminent is about the braking point
STAGE_SPEED_BP = [4.5, 13.4, 22.4, 31.3]  # 10/30/50/70 mph
APPROACH_DIST = [150.0, 300.0, 500.0, 800.0]
IMMINENT_DIST = [40.0, 80.0, 130.0, 200.0]

CRAWL_SPEED = 4.5  # m/s, ~10 mph

# steps shorter than the approach point plus this margin get only the imminent stage
CHAIN_MARGIN = 150.0  # m

TYPE_KINDS = {'off ramp': 'exit', 'merge': 'merge', 'fork': 'keep'}

# the ordinal suffix is required: without it road names ('onto A40 exit') read as exit numbers
ORDINAL_EXIT_RE = re.compile(r'(\d+)(?:st|nd|rd|th)\s+exit')

MAX_EXITS = 9  # renderers spell the exit number out, so it cannot run away

LEFTISH = ('left', 'slightLeft', 'sharpLeft', 'uturn')
RIGHTISH = ('right', 'slightRight', 'sharpRight')


def modifier_side(modifier: str) -> str:
  if modifier in LEFTISH:
    return 'left'
  if modifier in RIGHTISH:
    return 'right'
  return 'none'


def maneuver_event(maneuver_type: str, modifier: str, instruction: str = '') -> tuple[str, str, int] | None:
  """(kind, side, count) for a maneuver that deserves a prompt, or None for ones that don't."""
  if 'exit ro' in maneuver_type:  # 'exit roundabout'/'exit rotary': the entry cue covered it
    return None
  if any(t in maneuver_type for t in ROUNDABOUT_TYPES):
    exit_num = ORDINAL_EXIT_RE.search(instruction)
    return 'roundabout', modifier_side(modifier), min(MAX_EXITS, int(exit_num.group(1))) if exit_num else 0
  for prefix_type, kind in TYPE_KINDS.items():
    if prefix_type in maneuver_type:
      side = modifier_side(modifier)
      return (kind, side, 0) if side != 'none' else None
  if maneuver_type in ('continue', 'new name'):
    # Mapbox files divided-road u-turns under 'continue'
    return ('uturn', 'left', 0) if modifier == 'uturn' else None
  if maneuver_type in ('arrive', 'depart', 'notification'):
    return None
  if modifier == 'uturn':
    return 'uturn', 'left', 0
  side = modifier_side(modifier)
  if side == 'none':
    return None
  kind = 'slightTurn' if 'slight' in modifier else 'sharpTurn' if 'sharp' in modifier else 'turn'
  return kind, side, 0


class NavAudioCues:
  """Tracks route progress and yields at most one cue per update.

  Eligible cues that lose the priority race simply stay eligible: at 3 Hz the runner-up
  plays a cycle later, so nothing needs a queue.
  """

  def __init__(self):
    self.kind: str = ''
    self.stage: str = ''
    self.direction: str = 'none'
    self.count: int = 0
    self.cue_id: int = 0

    self._route = None
    self._fired: set = set()
    self._reroute_armed: bool = True
    self._arrived: bool = False

  def _fire(self, kind: str, stage: str, direction: str = 'none', count: int = 0, key: tuple | None = None) -> None:
    self.kind = kind
    self.stage = stage
    self.direction = direction
    self.count = count
    self.cue_id = (self.cue_id + 1) & 0xffffffff
    if key is not None:
      self._fired.add(key)

  def _reset_route(self, route) -> None:
    self._route = route
    self._fired = set()
    self._arrived = False

  def update(self, route, progress, guidance, v_ego: float, rerouting: bool) -> None:
    # a recompute rebuilds the route, so step indices, and every fired key, start over
    if route is not self._route:
      self._reset_route(route)

    if progress is None or route is None:
      return

    # one reroute cue per excursion: re-arms only once the car is back on the route
    if rerouting:
      if self._reroute_armed:
        self._reroute_armed = False
        self._fire('reroute', 'reroute')
        return
    else:
      self._reroute_armed = True

    if guidance.arrived and not self._arrived:
      self._arrived = True
      self._fire('arrive', 'arrive')
      return

    next_turn = progress.next_turn
    if next_turn is None:
      return
    nt_idx = progress.current_step_idx + 1
    event = maneuver_event(next_turn.maneuver, next_turn.modifier, next_turn.instruction)
    distance = progress.distance_to_end_of_step
    crawling = v_ego < CRAWL_SPEED

    if event is not None:
      kind, side, count = event
      imminent_at = float(interp(v_ego, STAGE_SPEED_BP, IMMINENT_DIST))
      approach_at = float(interp(v_ego, STAGE_SPEED_BP, APPROACH_DIST))

      if distance <= imminent_at and (nt_idx, 'imminent') not in self._fired:
        self._fired.add((nt_idx, 'approach'))
        self._fire(kind, 'imminent', side, count, (nt_idx, 'imminent'))
        return

      if distance <= approach_at and (nt_idx, 'approach') not in self._fired and not crawling:
        step_len = progress.current_step.distance
        if step_len < approach_at + CHAIN_MARGIN:
          self._fired.add((nt_idx, 'approach'))
        else:
          self._fire(kind, 'approach', side, count, (nt_idx, 'approach'))
          return

    hint = guidance.lane_change_direction
    if hint in ('left', 'right') and (nt_idx, 'lane', hint) not in self._fired and not crawling:
      self._fire('laneChange', 'lane', hint, 0, (nt_idx, 'lane', hint))
