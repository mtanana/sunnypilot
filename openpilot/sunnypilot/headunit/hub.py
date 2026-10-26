"""
Hands the latest telemetry snapshot from the collector loop to HTTP handler threads.

The collector only builds snapshots while someone is listening (an SSE client or a pending
/snapshot request), so an idle bridge does no JSON work.
"""
import json
import threading


class TelemetryHub:
  def __init__(self):
    self._cond = threading.Condition()
    self._seq = 0
    self._payload = b"{}"
    self._subscribers = 0
    self._snapshot_waiters = 0

  @property
  def wanted(self) -> bool:
    with self._cond:
      return self._subscribers > 0 or self._snapshot_waiters > 0

  @property
  def subscribers(self) -> int:
    with self._cond:
      return self._subscribers

  def publish(self, snapshot: dict) -> None:
    payload = json.dumps(snapshot, separators=(",", ":"), allow_nan=False).encode()
    with self._cond:
      self._seq += 1
      self._payload = payload
      self._cond.notify_all()

  def subscribe(self) -> None:
    with self._cond:
      self._subscribers += 1

  def unsubscribe(self) -> None:
    with self._cond:
      self._subscribers = max(0, self._subscribers - 1)

  def wait_next(self, last_seq: int, timeout: float) -> tuple[int, bytes | None]:
    """Block until a snapshot newer than last_seq exists. Returns (seq, payload or None on timeout)."""
    with self._cond:
      if self._cond.wait_for(lambda: self._seq > last_seq, timeout=timeout):
        return self._seq, self._payload
      return self._seq, None

  def fresh_snapshot(self, timeout: float) -> bytes | None:
    with self._cond:
      self._snapshot_waiters += 1
      start = self._seq
    try:
      return self.wait_next(start, timeout)[1]
    finally:
      with self._cond:
        self._snapshot_waiters -= 1
