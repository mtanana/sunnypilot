"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from urllib.parse import quote

import requests

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigation_helpers.route_line import overview_points

GEOCODING_URL = 'https://api.mapbox.com/geocoding/v5/mapbox.places'
# driving-traffic: durations include live traffic, so the ETA is an estimate rather than the
# free-flow floor the plain driving profile returns
DIRECTIONS_URL = 'https://api.mapbox.com/directions/v5/mapbox/driving-traffic'
# Mapbox's public timezone boundary tileset, queried per accepted route rather than shipping
# a coordinate-to-zone dataset on the device
TIMEZONE_URL = 'https://api.mapbox.com/v4/examples.4ze9z6tv/tilequery'


def _lonlat(c: Coordinate) -> str:
  return f'{c.longitude},{c.latitude}'


def _get_json(what: str, url: str, params: dict, timeout: float) -> dict | None:
  """One Mapbox request; None on any failure, logged, so no caller can take navd down."""
  try:
    response = requests.get(url, params=params, timeout=timeout)
    if response.status_code != 200:
      cloudlog.error("navd: %s failed with HTTP %d", what, response.status_code)
      return None
    return response.json()
  except requests.RequestException as e:
    cloudlog.warning("navd: %s request failed: %s", what, e)
  except ValueError as e:
    cloudlog.error("navd: could not parse %s response: %s", what, e)
  return None


class MapboxIntegration:
  def __init__(self):
    self.params = Params()

  def get_public_token(self) -> str:
    token: str = self.params.get('MapboxToken', return_default=True)
    return token

  def set_destination(self, destination: dict, position: Coordinate, bearing: float | None = None) -> tuple[dict, dict | None]:
    """Geocodes if needed, then requests and stores a route.

    Returns the destination (with coordinates and names filled in) and the stored route
    dict, or None when either call failed: a destination that geocodes but produces no
    route has not been accepted and must be retried.
    """
    if 'latitude' not in destination or 'longitude' not in destination:
      addr = destination['place_name']
      if not addr:
        return destination, None
      token = self.get_public_token()
      if not token:
        cloudlog.error("navd: geocoding skipped, no MapboxToken set")
        return destination, None

      data = _get_json("geocoding", f'{GEOCODING_URL}/{quote(addr)}.json',
                       {'access_token': token, 'limit': 1, 'proximity': _lonlat(position)}, timeout=5)
      features = (data or {}).get('features') or []
      if not features:
        if data is not None:
          cloudlog.warning("navd: geocoding found no match for destination %r", addr)
        return destination, None
      longitude, latitude = features[0]['geometry']['coordinates']
      # resolved_name is the human label for recents; place_name stays the raw destination
      # string because the route preference is matched against it
      destination.update({'latitude': latitude, 'longitude': longitude, 'name': addr,
                          'resolved_name': features[0].get('place_name', '')})

    return destination, self.request_route(destination, position, bearing)

  def request_route(self, destination: dict, start: Coordinate, bearing: float | None = None) -> dict | None:
    end = Coordinate(float(destination['latitude']), float(destination['longitude']))

    # a route preference exists only if the destination page stored one, and it names the
    # destination it was chosen for: a destination set any other way must get the fastest route
    preference = None
    stored = self.params.get('MapboxRoutePreference')
    if isinstance(stored, dict) and stored.get('dest') == destination.get('place_name'):
      preference = stored.get('summary')

    token = self.get_public_token()
    route = self.generate_route(start, end, token, bearing, preference)
    if not route:
      # storing an empty route here would discard a working one on a failed reroute, and would
      # read as an accepted destination that navd then has no reason to recompute
      cloudlog.error("navd: no route stored for destination %r, keeping any previous route", destination.get('name'))
      return None

    # the stored copy is what destinationd's /api/route serves
    self.params.put('MapboxSettings', {'navData': {'current': end.as_dict(), 'route': route}})

    # the device clock is GPS-synced UTC with no system timezone, so the ETA readout needs the
    # destination's zone. Cleared on failure: a zone left over from an earlier trip is worse
    # than the UI's device-local fallback
    tzid = self.get_timezone(end, token)
    if tzid:
      self.params.put('NavDestinationTimezone', tzid)
    else:
      self.params.remove('NavDestinationTimezone')
    return route

  def search_places(self, query: str, proximity: Coordinate | None = None, limit: int = 5) -> list[dict] | None:
    """Forward geocoding for the destination page: several candidates, not navd's single best match.

    Returns None when the request itself failed, [] when Mapbox found nothing, so the caller
    can tell an offline device from a bad query.
    """
    token = self.get_public_token()
    if not token or not query:
      return None
    params: dict = {'access_token': token, 'limit': limit}
    if proximity is not None:
      params['proximity'] = _lonlat(proximity)
    data = _get_json("search", f'{GEOCODING_URL}/{quote(query)}.json', params, timeout=10)
    if data is None:
      return None
    try:
      return [
        {'name': feature['place_name'], 'longitude': feature['geometry']['coordinates'][0],
         'latitude': feature['geometry']['coordinates'][1]}
        for feature in data['features']
      ]
    except (KeyError, IndexError, TypeError) as e:
      cloudlog.error("navd: could not parse search response for %r: %s", query, e)
      return None

  def preview_routes(self, start: Coordinate, end: Coordinate) -> list[dict] | None:
    """Route alternates with live and typical durations and their overview shapes, for the pick-a-route step.

    steps=true is required even though the steps are discarded: without it the leg summary
    comes back empty, and the summary is what identifies the chosen alternate later.
    """
    token = self.get_public_token()
    if not token:
      return None
    params = {'access_token': token, 'geometries': 'geojson', 'steps': 'true', 'overview': 'simplified', 'alternatives': 'true'}
    data = _get_json("route preview", f'{DIRECTIONS_URL}/{_lonlat(start)};{_lonlat(end)}', params, timeout=10)
    if data is None:
      return None
    if data.get('code') != 'Ok':
      cloudlog.error("navd: route preview returned no route (code=%s)", data.get('code'))
      return None
    try:
      return [
        {
          'summary': (route.get('legs') or [{}])[0].get('summary', ''),
          'distance': route['distance'],
          'duration': route['duration'],
          'durationTypical': route.get('duration_typical', route['duration']),
          'points': overview_points((route.get('geometry') or {}).get('coordinates') or []),
        }
        for route in data['routes']
      ]
    except (KeyError, IndexError, TypeError) as e:
      cloudlog.error("navd: could not parse route preview response: %s", e)
      return None

  @staticmethod
  def get_timezone(position: Coordinate, token: str) -> str | None:
    data = _get_json("timezone lookup", f'{TIMEZONE_URL}/{_lonlat(position)}.json', {'access_token': token}, timeout=5)
    if data is None:
      return None
    try:
      features = data['features']
      if features:
        return features[0]['properties']['TZID']
      cloudlog.warning("navd: no timezone found at %s", _lonlat(position))
    except (KeyError, IndexError, TypeError) as e:
      cloudlog.error("navd: could not parse timezone response: %s", e)
    return None

  @staticmethod
  def _select_route(routes: list, preference: str | None) -> dict:
    """The fastest route unless the driver picked an alternate by its leg summary.

    Preferences survive reroutes on purpose: mid-trip the preferred road is usually still on
    offer, and when it no longer applies the fastest route is the right fallback anyway.
    """
    if preference:
      for route in routes:
        legs = route.get('legs') or []
        if legs and legs[0].get('summary') == preference:
          cloudlog.warning("navd: using preferred route %r", preference)
          return route
      cloudlog.warning("navd: preferred route %r not offered, using the fastest", preference)
    return routes[0]

  @staticmethod
  def generate_route(start: Coordinate, end: Coordinate, token: str, bearing: float | None = None,
                     preference: str | None = None) -> dict | None:
    if not token:
      cloudlog.error("navd: route generation skipped, no MapboxToken set")
      return None

    params = {
      'access_token': token,
      'geometries': 'geojson',
      'steps': 'true',
      'overview': 'full',
      'annotations': 'maxspeed',
      # alternates cost nothing extra per request and are what lets a stored route preference
      # actually pick the road the driver chose on the destination page
      'alternatives': 'true',
      'banner_instructions': 'true',
    }
    if bearing is not None:
      params['bearings'] = f'{int((bearing + 360) % 360):.0f},90;'

    data = _get_json("directions", f'{DIRECTIONS_URL}/{_lonlat(start)};{_lonlat(end)}', params, timeout=5)
    if data is None:
      return None
    routes = data.get('routes')
    if data.get('code') != 'Ok' or not routes or not routes[0].get('legs'):
      cloudlog.error("navd: directions returned no usable route (code=%s)", data.get('code'))
      return None

    route = MapboxIntegration._select_route(routes, preference)
    leg = route['legs'][0]
    steps = [
      {
        'maneuver': step['maneuver']['type'],
        'instruction': step['maneuver']['instruction'],
        'distance': step['distance'],
        'duration': step['duration'],
        'location': {'longitude': step['maneuver']['location'][0], 'latitude': step['maneuver']['location'][1]},
        'modifier': step['maneuver'].get('modifier', 'none'),
        'bannerInstructions': step['bannerInstructions'],
      }
      for step in leg['steps']
    ]
    # one entry per geometry segment; an unknown segment keeps its slot so indices stay aligned
    maxspeed = [{'speed': item['speed'], 'unit': item['unit']} if 'speed' in item else None for item in leg['annotation']['maxspeed']]
    return {
      'steps': steps,
      'totalDistance': route['distance'],
      'totalDuration': route['duration'],
      'geometry': [{'longitude': coord[0], 'latitude': coord[1]} for coord in route['geometry']['coordinates']],
      'maxspeed': maxspeed,
    }
