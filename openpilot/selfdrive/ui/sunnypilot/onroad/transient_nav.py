"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

How much of the navigation display is showing; the renderers (3X rail, mici corner) own
what each state looks like. Approach timing is navigationd's: the machine edge-detects its
cues (audioCueId increments once per cue) rather than re-deriving windows from speed.
"""
from enum import IntEnum

from openpilot.selfdrive.ui.sunnypilot.nav_status import NavState


class TransientNavState(IntEnum):
  OFF = 0
  QUIET = 1     # between maneuvers: only the chip
  APPROACH = 2  # auto-expanded until the maneuver passes
  PINNED = 3    # expanded by the driver, across maneuvers


EXPAND_STAGES = ('approach', 'imminent')


class ChipMode(IntEnum):
  HIDDEN = 0
  SEARCHING = 1
  FAILURE = 2
  LIVE = 3


def chip_mode(status) -> ChipMode:
  if not status.allow_navigation or not status.show_turn_indicator or \
      status.state in (NavState.OFFLINE, NavState.NO_DESTINATION):
    return ChipMode.HIDDEN
  if status.state in (NavState.WAITING_FOR_GPS, NavState.COMPUTING):
    return ChipMode.SEARCHING
  if status.state == NavState.NO_ROUTE:
    return ChipMode.FAILURE
  return ChipMode.LIVE


# pole only until the localizer has a fix, pole plus banner once the wait is on Mapbox
def flag_raised(state: NavState) -> bool:
  return state != NavState.WAITING_FOR_GPS


# allManeuvers[0] is the step being driven, its maneuver already behind the car; the 'arrive'
# step can be the only one left
def pick_upcoming_index(maneuvers) -> int | None:
  if len(maneuvers) > 1:
    return 1
  if len(maneuvers) == 1 and maneuvers[0].type == 'arrive':
    return 0
  return None


def pick_upcoming_maneuver(maneuvers) -> tuple[str, str, float] | None:
  idx = pick_upcoming_index(maneuvers)
  if idx is None:
    return None
  m = maneuvers[idx]
  return m.type, m.modifier, m.distance


def maneuver_signature(maneuvers) -> tuple[int, str, str] | None:
  # the list shrinks by one per passed step and is rebuilt on a reroute, so its length tells
  # apart consecutive maneuvers of the same type and modifier
  m = pick_upcoming_maneuver(maneuvers)
  return None if m is None else (len(maneuvers), m[0], m[1])


class TransientNav:
  def __init__(self):
    self.state = TransientNavState.OFF
    self._last_cue_id: int | None = None
    self._signature: tuple | None = None
    self._dismissed: tuple | None = None

  def update(self, active: bool, maneuvers, cue_id: int, cue_stage: str, off_route: bool = False) -> TransientNavState:
    if not active:
      self.state = TransientNavState.OFF
      self._last_cue_id = None
      self._signature = None
      self._dismissed = None
      return self.state

    if self.state == TransientNavState.OFF:
      self.state = TransientNavState.QUIET

    signature = maneuver_signature(maneuvers)
    if signature != self._signature:
      self._signature = signature
      self._dismissed = None
      if self.state == TransientNavState.APPROACH:
        self.state = TransientNavState.QUIET

    # PINNED stays up off route: the driver asked for it
    if off_route and self.state == TransientNavState.APPROACH:
      self.state = TransientNavState.QUIET

    if self._last_cue_id is None:
      # the cue fields are sticky, so the first observed id may be long stale
      self._last_cue_id = cue_id
    elif cue_id != self._last_cue_id:
      self._last_cue_id = cue_id
      if cue_stage in EXPAND_STAGES and self.state == TransientNavState.QUIET \
          and not off_route and signature is not None and signature != self._dismissed:
        self.state = TransientNavState.APPROACH
    return self.state

  def on_tap(self) -> None:
    if self.state == TransientNavState.QUIET:
      self.state = TransientNavState.PINNED
    elif self.state == TransientNavState.PINNED:
      self.state = TransientNavState.QUIET
    elif self.state == TransientNavState.APPROACH:
      self.state = TransientNavState.QUIET
      self._dismissed = self._signature
