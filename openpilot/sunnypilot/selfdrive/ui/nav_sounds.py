"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

How a navigation cue sounds; navigationd only says what it means. Four sounds: a pair whose
contour is the direction (rising right, falling left, doubled when imminent), a lane blip, a
reroute pair and an arrival arpeggio. The kind of turn lives on the screen, not in the sound.
"""
import threading

import numpy as np

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

SAMPLE_RATE = 48000
BASE_FREQ = 587.33  # D5: the low anchor note has to clear road noise on the device speaker
AMPLITUDE = 0.85
EDGE_S = 0.005  # raised-cosine attack/release


def _tone(freq: float, seconds: float, sr: int = SAMPLE_RATE, amp: float = AMPLITUDE) -> np.ndarray:
  n = max(1, int(seconds * sr))
  wave = amp * np.sin(2 * np.pi * freq * np.arange(n) / sr).astype(np.float32)
  return wave * _envelope(n, sr)


def _envelope(n: int, sr: int) -> np.ndarray:
  env = np.ones(n, dtype=np.float32)
  e = min(int(EDGE_S * sr), n // 2)
  if e > 0:
    ramp = (0.5 * (1 - np.cos(np.pi * np.arange(e) / e))).astype(np.float32)
    env[:e] = ramp
    env[-e:] = ramp[::-1]
  return env


def _gap(seconds: float, sr: int = SAMPLE_RATE) -> np.ndarray:
  return np.zeros(max(0, int(seconds * sr)), dtype=np.float32)


def _note(semitones: float, seconds: float, sr: int, amp: float = AMPLITUDE) -> np.ndarray:
  return _tone(BASE_FREQ * 2 ** (semitones / 12), seconds, sr, amp)


def _pair(direction: str, note_dur: float, gap_dur: float, sr: int) -> list[np.ndarray]:
  """The directional two-note shape: contour is the entire message."""
  step = {'right': 7, 'left': -7}.get(direction, 0)
  return [_note(0, note_dur, sr), _gap(gap_dur, sr), _note(step, note_dur, sr)]


def earcon_wave(kind: str, stage: str, direction: str = 'none', sr: int = SAMPLE_RATE) -> np.ndarray:
  if kind == 'reroute':
    return np.concatenate([_note(-2, 0.12, sr, amp=0.55), _gap(0.03, sr), _note(-3, 0.12, sr, amp=0.55)])
  if kind == 'arrive':
    return np.concatenate([_note(0, 0.09, sr), _gap(0.03, sr), _note(4, 0.09, sr), _gap(0.03, sr), _note(7, 0.09, sr)])
  if kind == 'laneChange':
    step = 4 if direction == 'right' else -4
    return np.concatenate([_note(3, 0.06, sr), _gap(0.03, sr), _note(3 + step, 0.06, sr)])

  fast = stage == 'imminent'
  note_dur = 0.055 if fast else 0.09
  gap_dur = 0.02 if fast else 0.03
  parts = _pair(direction, note_dur, gap_dur, sr)
  if fast:
    parts += [_gap(0.12, sr)] + _pair(direction, note_dur, gap_dur, sr)
  return np.concatenate(parts)


class NavAudioPlayer:
  """Feeds nav cue samples to soundd's mixer.

  Owns the edge detection on audioCueId and the NavigationAudio param; soundd only asks for
  frames and decides whether the channel is free (no alert playing, quiet mode off).

  update() runs on soundd's 20 Hz loop and the rest on the PortAudio callback thread, so
  the buffer and the read position are only ever touched together, under _lock. Synthesis
  stays outside it: the callback must never wait on a cue being built.
  """

  def __init__(self, sr: int = SAMPLE_RATE):
    self.params = Params()
    self.sr = sr
    self.enabled = False
    self._frame = 0
    self._last_cue_id: int | None = None
    self._buf = np.zeros(0, dtype=np.float32)
    self._pos = 0
    self._lock = threading.Lock()
    self._bad_kinds: set[str] = set()
    self._read_params()

  def _read_params(self) -> None:
    self.enabled = self.params.get_bool('NavigationAudio')

  def load_params(self) -> None:
    self._frame += 1
    if self._frame % 50 == 0:  # 2.5 seconds
      self._read_params()

  def update(self, sm) -> None:
    if not sm.updated['navigationd']:
      return
    nav = sm['navigationd']
    cue_id = nav.audioCueId
    if self._last_cue_id is None:
      # cues are sticky on the message, so the one that predates this process must not play
      self._last_cue_id = cue_id
      return
    if cue_id == self._last_cue_id:
      return
    self._last_cue_id = cue_id
    kind = str(nav.audioCueKind)
    stage = str(nav.audioCueStage)
    if not self.enabled or not kind:
      return
    try:
      buf = earcon_wave(kind, stage, str(nav.audioCueDirection), sr=self.sr)
    except Exception:
      # a newer navigationd, or a replayed log, can carry kinds this build cannot render;
      # letting that out would abort the stream and take soundd down with it
      if kind not in self._bad_kinds:
        self._bad_kinds.add(kind)
        cloudlog.exception(f"nav audio: unrenderable cue kind {kind!r}")
      return
    # a newer cue carries newer information, so it replaces whatever was still playing
    with self._lock:
      self._buf = buf
      self._pos = 0

  @property
  def active(self) -> bool:
    with self._lock:
      return self._pos < len(self._buf)

  def cancel(self) -> None:
    with self._lock:
      self._buf = np.zeros(0, dtype=np.float32)
      self._pos = 0

  def get_frames(self, frames: int) -> np.ndarray:
    out = np.zeros(frames, dtype=np.float32)
    with self._lock:
      take = min(frames, len(self._buf) - self._pos)
      if take > 0:
        out[:take] = self._buf[self._pos:self._pos + take]
        self._pos += take
    return out
