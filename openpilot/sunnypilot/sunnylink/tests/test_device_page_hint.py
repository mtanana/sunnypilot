"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.sunnylink.tools.generate_settings_schema import generate_schema
from openpilot.sunnypilot.sunnylink.utils import DEVICE_PAGE_PLACEHOLDER, device_page_hint, fill_device_page
from openpilot.common.test import OpenpilotTestCase


def _panel(schema):
  return next(panel for panel in schema["panels"] if panel["id"] == "navigation")


class TestDevicePageHint(OpenpilotTestCase):
  def test_wifi_lease_is_the_wifi_address(self):
    assert device_page_hint("192.168.1.42") == "http://192.168.1.42:5050"

  def test_tethering_lease_is_the_tethering_address(self):
    assert device_page_hint("192.168.43.1") == "http://192.168.43.1:5050 (Wifi Tethering)"

  def test_no_network_says_how_to_reach_it(self):
    assert device_page_hint("") == "the device's wifi address once it joins a network, or http://192.168.43.1:5050 once you enable Wifi Tethering"

  def test_navigation_panel_carries_the_placeholder_until_served(self):
    assert DEVICE_PAGE_PLACEHOLDER in _panel(generate_schema())["description"]

  def test_served_schema_names_the_page(self):
    description = _panel(fill_device_page(generate_schema(), device_page_hint("10.0.0.7")))["description"]
    assert DEVICE_PAGE_PLACEHOLDER not in description
    assert "at http://10.0.0.7:5050." in description
