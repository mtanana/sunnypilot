"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import openpilot.cereal.messaging as messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.test import OpenpilotTestCase


class NavdTestCase(OpenpilotTestCase):
  """The base gives every test its own params and msgq directory; this PubMaster creates
  every socket in it, since a SubMaster raises on a socket nobody has published to yet."""

  def setUp(self):
    super().setUp()
    self._navd_pm = messaging.PubMaster(list(SERVICE_LIST))

  def tearDown(self):
    del self._navd_pm
    super().tearDown()
