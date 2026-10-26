"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import openpilot.cereal.messaging as messaging

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.tests.fake_submaster import FakeSubMaster


class TestFakeSubMasterContract(OpenpilotTestCase):
  """The fake's whole worth is matching the real attribute surface; this is the check
  that keeps it true when messaging's SubMaster changes underneath it."""

  def setUp(self):
    super().setUp()
    self.pm = messaging.PubMaster(['carState'])
    self.real = messaging.SubMaster(['carState'])
    self.fake = FakeSubMaster({'carState': messaging.new_message('carState')})

  def test_every_fake_attribute_exists_on_the_real_thing(self):
    invented = [name for name in vars(self.fake) if not hasattr(self.real, name)]
    assert invented == [], f"FakeSubMaster invents attributes a real SubMaster lacks: {invented}"

  def test_shared_attributes_share_their_types(self):
    for name, value in vars(self.fake).items():
      assert isinstance(value, type(getattr(self.real, name))), name

  def test_typos_fail_like_the_real_object(self):
    # the exact spelling that crashed plannerd onroad while MagicMocks stayed green
    with self.assertRaises(AttributeError):
      self.fake.rcv_time  # noqa: B018
    with self.assertRaises(AttributeError):
      self.real.rcv_time  # noqa: B018

  def test_indexing_returns_the_service_struct(self):
    msg = messaging.new_message('navigationd')
    msg.navigationd.currentSpeedLimit = 42
    fake = FakeSubMaster({'navigationd': msg})
    assert fake['navigationd'].currentSpeedLimit == 42
    with self.assertRaises(KeyError):
      fake['carState']

  def test_header_valid_lands_in_the_valid_dict(self):
    msg = messaging.new_message('carState')
    msg.valid = True
    fake = FakeSubMaster({'carState': msg})
    assert fake.valid['carState'] is True
