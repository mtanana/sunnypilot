"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.navd.navigation_helpers import destination_store as ds
from openpilot.sunnypilot.navd.navigation_helpers.destination_store import DestinationStore


class TestFavoritesFunctions(OpenpilotTestCase):
  def test_settings_ui_shape_is_preserved(self):
    # the settings UI reads home/work at the top level and named entries under "favorites";
    # a store round-trip must hand back exactly that shape
    favs = ds.set_favorite({}, "", "123 Home St", kind="home")
    favs = ds.set_favorite(favs, "Gym", "456 Gym Ave")
    assert favs == {"home": "123 Home St", "favorites": {"Gym": "456 Gym Ave"}}

  def test_view_orders_home_work_then_named_as_stored(self):
    favs = {"work": "w", "home": "h", "favorites": {"zeta": "z", "Alpha": "a"}}
    view = ds.favorites_view(favs)
    assert [entry["name"] for entry in view] == ["Home", "Work", "zeta", "Alpha"]
    assert [entry["dest"] for entry in view] == ["h", "w", "z", "a"]

  def test_new_favorites_append_and_resaves_keep_their_place(self):
    favs = ds.set_favorite({}, "Gym", "g")
    favs = ds.set_favorite(favs, "Cafe", "c")
    favs = ds.set_favorite(favs, "Gym", "g2")
    assert list(favs["favorites"]) == ["Gym", "Cafe"]
    assert favs["favorites"]["Gym"] == "g2"

  def test_reorder_puts_named_favorites_in_the_given_order(self):
    favs = {"home": "h", "favorites": {"a": "1", "b": "2", "c": "3"}}
    favs = ds.reorder_favorites(favs, ["c", "a"])
    assert list(favs["favorites"]) == ["c", "a", "b"]
    assert [entry["name"] for entry in ds.favorites_view(favs)] == ["Home", "c", "a", "b"]
    # unknown names are ignored and garbage leaves the order alone
    assert list(ds.reorder_favorites(favs, ["zzz", " b "])["favorites"]) == ["b", "c", "a"]
    assert ds.reorder_favorites(favs, "b") == favs

  def test_remove_favorite_by_kind_and_name(self):
    favs = {"home": "h", "favorites": {"Gym": "g"}}
    favs = ds.remove_favorite(favs, kind="home")
    assert favs == {"favorites": {"Gym": "g"}}
    favs = ds.remove_favorite(favs, "Gym")
    assert favs == {}

  def test_garbage_input_normalizes_to_empty(self):
    assert ds.normalize_favorites(None) == {}
    assert ds.normalize_favorites("not a dict") == {}
    assert ds.normalize_favorites({"home": 42, "favorites": {"": "x", "y": "  "}}) == {}
    assert ds.favorites_view(None) == []

  def test_set_favorite_ignores_empty_dest(self):
    assert ds.set_favorite({}, "Gym", "   ") == {}

  def test_route_binding_lives_and_dies_with_its_favorite(self):
    favs = ds.set_favorite({}, "", "-122.1,47.6", kind="work", summary="I-5 South")
    assert favs["routes"] == {"-122.1,47.6": "I-5 South"}
    assert ds.favorites_view(favs)[0]["summary"] == "I-5 South"
    favs = ds.remove_favorite(favs, kind="work")
    assert favs == {}

  def test_saving_without_a_route_unbinds(self):
    favs = ds.set_favorite({}, "", "-122.1,47.6", kind="work", summary="I-5 South")
    favs = ds.set_favorite(favs, "", "-122.1,47.6", kind="work")
    assert favs == {"work": "-122.1,47.6"}

  def test_binding_for_an_overwritten_dest_is_pruned(self):
    favs = ds.set_favorite({}, "", "old dest", kind="home", summary="I-5 South")
    favs = ds.set_favorite(favs, "", "new dest", kind="home")
    assert favs == {"home": "new dest"}


class TestRecentsFunctions(OpenpilotTestCase):
  def test_most_recent_first_and_dedupe_on_dest(self):
    recents = ds.update_recents([], "Work", "-122.1,47.6")
    recents = ds.update_recents(recents, "Gym", "-122.2,47.7")
    recents = ds.update_recents(recents, "Work again", "-122.1,47.6")
    assert [entry["dest"] for entry in recents] == ["-122.1,47.6", "-122.2,47.7"]
    assert recents[0]["name"] == "Work again"

  def test_capped_at_limit(self):
    recents: list = []
    for i in range(15):
      recents = ds.update_recents(recents, f"place {i}", f"dest {i}")
    assert len(recents) == ds.RECENTS_LIMIT
    assert recents[0]["dest"] == "dest 14"

  def test_name_falls_back_to_dest(self):
    recents = ds.update_recents([], "", "740 E Ventura Blvd")
    assert recents[0]["name"] == "740 E Ventura Blvd"

  def test_keep_existing_name_preserves_a_chosen_label(self):
    recents = ds.update_recents([], "Library", "-122.1,47.6")
    recents = ds.update_recents(recents, "123 Main St", "-122.1,47.6", keep_existing_name=True)
    assert recents == [{"name": "Library", "dest": "-122.1,47.6"}]

  def test_keep_existing_name_still_backfills_a_default_label(self):
    # an entry whose name is just its dest never got a real label, so the backfill wins
    recents = ds.update_recents([], "", "-122.1,47.6")
    recents = ds.update_recents(recents, "123 Main St", "-122.1,47.6", keep_existing_name=True)
    assert recents == [{"name": "123 Main St", "dest": "-122.1,47.6"}]

  def test_garbage_entries_dropped(self):
    assert ds.normalize_recents(None) == []
    assert ds.normalize_recents([{"name": "no dest"}, "junk", {"dest": " ok ", "name": ""}]) == [{"name": "ok", "dest": "ok"}]


class TestDestinationStore(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.params = Params()
    self.store = DestinationStore(self.params)

  def test_favorites_round_trip(self):
    self.store.set_favorite("", "123 Home St", kind="home")
    self.store.set_favorite("Gym", "456 Gym Ave")
    assert self.params.get("MapboxFavorites") == {"home": "123 Home St", "favorites": {"Gym": "456 Gym Ave"}}
    assert [entry["name"] for entry in self.store.favorites()] == ["Home", "Gym"]
    self.store.set_favorite("Cafe", "789 Cafe Rd")
    self.store.reorder_favorites(["Cafe", "Gym"])
    assert [entry["name"] for entry in self.store.favorites()] == ["Home", "Cafe", "Gym"]
    self.store.remove_favorite("Cafe")
    self.store.remove_favorite("Gym")
    self.store.remove_favorite(kind="home")
    assert self.store.favorites() == []

  def test_set_destination_writes_route_and_recent(self):
    self.store.set_destination("-122.1,47.6", name="Work")
    assert self.params.get("MapboxRoute") == "-122.1,47.6"
    assert self.params.get("MapboxRoutePreference") is None
    assert self.store.recents()[0] == {"name": "Work", "dest": "-122.1,47.6"}

  def test_route_preference_is_bound_to_its_destination(self):
    self.store.set_destination("-122.1,47.6", name="Work", route_summary="I-5 South")
    assert self.params.get("MapboxRoutePreference") == {"dest": "-122.1,47.6", "summary": "I-5 South"}
    # a later set without a chosen route must not leave the old preference behind
    self.store.set_destination("-122.2,47.7", name="Gym")
    assert self.params.get("MapboxRoutePreference") is None

  def test_clear_destination_clears_preference(self):
    self.store.set_destination("-122.1,47.6", route_summary="I-5 South")
    self.store.clear_destination()
    # Params reads an empty string param back as None
    assert not self.params.get("MapboxRoute")
    assert self.params.get("MapboxRoutePreference") is None

  def test_empty_destination_is_a_no_op(self):
    self.store.set_destination("   ")
    assert self.params.get("MapboxRoute") is None
    assert self.store.recents() == []
