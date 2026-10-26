"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from typing import Any

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

# MapboxFavorites keeps the shape the settings UI already reads and writes:
# {"home": "<dest>", "work": "<dest>", "favorites": {"<name>": "<dest>"}}.
# A route-bound favorite adds "routes": {"<dest>": "<summary>"}; the settings UI rewrites
# the dict wholesale so the extra key survives its edits untouched. The named dict's
# insertion order is the display order (JSON and Python both keep it, and the settings
# UI writes it back as read), so a reorder rewrites the dict in the new order.
# MapboxRecents is a most-recent-first list of {"name": "<label>", "dest": "<dest>"}.
# A dest is whatever MapboxRoute accepts: free text or a "lon,lat" string.

RECENTS_LIMIT = 10
FAVORITE_KINDS = ("home", "work")


def _clean(value: Any) -> str:
  return str(value).strip() if isinstance(value, str) else ""


def _favorite_dests(favs: dict) -> set[str]:
  return {favs[kind] for kind in FAVORITE_KINDS if kind in favs} | set(favs.get("favorites", {}).values())


def normalize_favorites(favs: Any) -> dict:
  if not isinstance(favs, dict):
    return {}
  normalized: dict = {}
  for kind in FAVORITE_KINDS:
    if dest := _clean(favs.get(kind)):
      normalized[kind] = dest
  named = favs.get("favorites")
  if isinstance(named, dict):
    cleaned = {name: dest for name, dest in ((_clean(k), _clean(v)) for k, v in named.items()) if name and dest}
    if cleaned:
      normalized["favorites"] = cleaned
  routes = favs.get("routes")
  if isinstance(routes, dict):
    # a binding lives only as long as some favorite still points at its dest
    referenced = _favorite_dests(normalized)
    cleaned = {dest: summary for dest, summary in ((_clean(k), _clean(v)) for k, v in routes.items()) if dest and summary and dest in referenced}
    if cleaned:
      normalized["routes"] = cleaned
  return normalized


def favorites_view(favs: Any) -> list[dict]:
  """Flat listing for the API: home, then work, then named favorites in their stored order."""
  favs = normalize_favorites(favs)
  routes = favs.get("routes", {})
  view = [{"kind": kind, "name": kind.capitalize(), "dest": favs[kind]} for kind in FAVORITE_KINDS if kind in favs]
  view += [{"kind": "favorite", "name": name, "dest": dest} for name, dest in favs.get("favorites", {}).items()]
  for entry in view:
    if summary := routes.get(entry["dest"]):
      entry["summary"] = summary
  return view


def set_favorite(favs: Any, name: str, dest: str, kind: str | None = None, summary: str = "") -> dict:
  favs = normalize_favorites(favs)
  dest = _clean(dest)
  name = _clean(name)
  if not dest:
    return favs
  if kind in FAVORITE_KINDS:
    favs[kind] = dest
  elif name:
    favs.setdefault("favorites", {})[name] = dest
  else:
    return favs
  # the save captures the current route pick exactly: a summary binds it, no summary unbinds,
  # so re-saving a favorite without a picked route predictably returns it to fastest-route
  routes = favs.setdefault("routes", {})
  if summary := _clean(summary):
    routes[dest] = summary
  else:
    routes.pop(dest, None)
  return normalize_favorites(favs)


def remove_favorite(favs: Any, name: str = "", kind: str | None = None) -> dict:
  favs = normalize_favorites(favs)
  if kind in FAVORITE_KINDS:
    favs.pop(kind, None)
  else:
    named = favs.get("favorites", {})
    named.pop(_clean(name), None)
    if not named:
      favs.pop("favorites", None)
  # re-normalizing prunes any binding the removed favorite was the last reference to
  return normalize_favorites(favs)


def reorder_favorites(favs: Any, names: Any) -> dict:
  """Named favorites in the given order; names not listed keep their relative order after them."""
  favs = normalize_favorites(favs)
  named = favs.get("favorites", {})
  wanted = [name for name in (_clean(n) for n in (names if isinstance(names, list) else [])) if name in named]
  ordered = {name: named[name] for name in wanted}
  ordered.update({name: dest for name, dest in named.items() if name not in ordered})
  if ordered:
    favs["favorites"] = ordered
  return favs


def normalize_recents(recents: Any) -> list[dict]:
  if not isinstance(recents, list):
    return []
  normalized = []
  for entry in recents:
    if isinstance(entry, dict) and (dest := _clean(entry.get("dest"))):
      normalized.append({"name": _clean(entry.get("name")) or dest, "dest": dest})
  return normalized[:RECENTS_LIMIT]


def update_recents(recents: Any, name: str, dest: str, limit: int = RECENTS_LIMIT, keep_existing_name: bool = False) -> list[dict]:
  """Most-recent-first, deduplicated on the dest string so a re-set trip moves up instead of doubling.

  keep_existing_name is for backfill writers (navd's acceptance record): a label the phone or
  page chose deliberately must not be clobbered by a geocoder's address for the same dest.
  """
  dest = _clean(dest)
  current = normalize_recents(recents)
  if not dest:
    return current
  name = _clean(name) or dest
  if keep_existing_name:
    existing = next((entry["name"] for entry in current if entry["dest"] == dest and entry["name"] != entry["dest"]), "")
    name = existing or name
  updated = [{"name": name, "dest": dest}]
  updated += [entry for entry in current if entry["dest"] != dest]
  return updated[:limit]


class DestinationStore:
  """Params boundary for destinations, favorites, and recents.

  Setting and clearing the route goes through here so the route preference param can never
  disagree with the destination it was chosen for.
  """

  def __init__(self, params: Params | None = None):
    self.params = params or Params()

  def favorites(self) -> list[dict]:
    return favorites_view(self.params.get("MapboxFavorites"))

  def set_favorite(self, name: str, dest: str, kind: str | None = None, summary: str = "") -> None:
    self.params.put("MapboxFavorites", set_favorite(self.params.get("MapboxFavorites"), name, dest, kind, summary), block=True)

  def remove_favorite(self, name: str = "", kind: str | None = None) -> None:
    self.params.put("MapboxFavorites", remove_favorite(self.params.get("MapboxFavorites"), name, kind), block=True)

  def reorder_favorites(self, names: list) -> None:
    self.params.put("MapboxFavorites", reorder_favorites(self.params.get("MapboxFavorites"), names), block=True)

  def recents(self) -> list[dict]:
    return normalize_recents(self.params.get("MapboxRecents"))

  def record_recent(self, name: str, dest: str, keep_existing_name: bool = False) -> None:
    self.params.put("MapboxRecents", update_recents(self.params.get("MapboxRecents"), name, dest,
                                                    keep_existing_name=keep_existing_name), block=True)

  def active_destination(self) -> str:
    return self.params.get("MapboxRoute") or ""

  def set_destination(self, dest: str, name: str = "", route_summary: str = "") -> None:
    dest = _clean(dest)
    if not dest:
      return
    cloudlog.warning("destinationd: destination set to %r (route %r)", name or dest, route_summary or "fastest")
    if route_summary:
      # the preference records which destination it was chosen for; navd ignores it on mismatch,
      # so a destination set later through athena or the settings UI cannot inherit it
      self.params.put("MapboxRoutePreference", {"dest": dest, "summary": route_summary}, block=True)
    else:
      self.params.remove("MapboxRoutePreference")
    self.params.put("MapboxRoute", dest, block=True)
    self.record_recent(name, dest)

  def clear_destination(self, source: str = "destinationd") -> None:
    # navd drops the route once the empty destination persists across two of its polls; the log
    # line attributes the clear to a deliberate request, matching the settings UI idiom
    cloudlog.warning("%s: destination cleared remotely", source)
    self.params.put("MapboxRoute", "", block=True)
    self.params.remove("MapboxRoutePreference")
