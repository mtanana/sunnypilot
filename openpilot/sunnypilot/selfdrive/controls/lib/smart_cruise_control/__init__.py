"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.constants import CV

MIN_V = 20 * CV.KPH_TO_MS  # Do not operate under 20 km/h
# the route's sharp and U-turn targets sit under MIN_V (nav-turn-slowdown-data.md)
NAV_TURN_MIN_V = 10 * CV.KPH_TO_MS
