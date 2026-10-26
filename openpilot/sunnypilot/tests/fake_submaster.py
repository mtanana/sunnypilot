"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from time import monotonic


class FakeSubMaster:
  """A SubMaster stand-in built from real capnp messages.

  A MagicMock SubMaster accepts any attribute, so a typo like sm.rcv_time passes the
  suite and crashes onroad (it did: plannerd, 2026-08-15, recv_time is the spelling).
  This fake defines only attributes a real SubMaster has, which the contract test in
  test_fake_submaster keeps true, and everything else raises AttributeError the way
  the real object would.
  """

  def __init__(self, messages: dict):
    """messages maps a service name to the event built by messaging.new_message(service).
    Like the real update loop, the header's valid lands in sm.valid and the service
    struct is what indexing returns; struct fields stay writable for per-test tweaks."""
    services = list(messages)
    now = monotonic()
    self.frame = 0
    self.services = services
    self.seen = dict.fromkeys(services, True)
    self.updated = dict.fromkeys(services, True)
    self.recv_time = dict.fromkeys(services, now)
    self.recv_frame = dict.fromkeys(services, 0)
    self.alive = dict.fromkeys(services, True)
    self.valid = {s: msg.valid for s, msg in messages.items()}
    self.logMonoTime = {s: msg.logMonoTime for s, msg in messages.items()}
    self.data = {s: getattr(msg, s) for s, msg in messages.items()}

  def __getitem__(self, s: str):
    return self.data[s]
