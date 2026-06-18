"""Tests for the pure build-mapping logic in opgg_runes."""

import pytest

import opgg_runes


# A realistic OP.GG rune entry (Jinx ADC shape).
RAW_RUNE = {
    "primary_page_id": 8000,
    "primary_rune_ids": [8005, 9101, 9104, 8014],
    "secondary_page_id": 8300,
    "secondary_rune_ids": [8313, 8321],
    "stat_mod_ids": [5005, 5008, 5011],
    "play": 1000,
    "win": 520,
    "pick_rate": 0.36,
}


# --- name normalisation / lookup ------------------------------------------- #
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Kai'Sa", "kaisa"),
        ("Lee Sin", "leesin"),
        ("Dr. Mundo", "drmundo"),
        ("Nunu & Willump", "nunuwillump"),
        ("Bel'Veth", "belveth"),
    ],
)
def test_slug(name, expected):
    assert opgg_runes._slug(name) == expected


def test_lookup_exact_and_prefix():
    idx = {"leesin": {"id": 64, "name": "Lee Sin"}, "kogmaw": {"id": 96, "name": "Kog'Maw"}}
    assert opgg_runes._lookup("Lee Sin", idx)["id"] == 64
    assert opgg_runes._lookup("kog", idx)["id"] == 96  # forgiving prefix match
    assert opgg_runes._lookup("zzz", idx) is None


def test_resolve_champion(monkeypatch):
    idx = {"leesin": {"id": 64, "name": "Lee Sin"}}
    monkeypatch.setattr(opgg_runes, "champion_index", lambda refresh=False: idx)
    assert opgg_runes.resolve_champion("Lee Sin")["id"] == 64
    with pytest.raises(ValueError):
        opgg_runes.resolve_champion("Nonexistent Champion")


# --- URL shape per mode ----------------------------------------------------- #
def test_build_url_ranked_uses_position():
    assert opgg_runes._build_url("global", "ranked", 64, "jungle").endswith("/ranked/64/jungle")


def test_build_url_aram_forces_none_position():
    # The passed position is ignored for positionless modes.
    assert opgg_runes._build_url("global", "aram", 64, "jungle").endswith("/aram/64/none")


def test_build_url_arena_has_no_position_segment():
    assert opgg_runes._build_url("global", "arena", 64, "x").endswith("/arena/64")


# --- RunePage --------------------------------------------------------------- #
def test_runepage_from_opgg_fields():
    page = opgg_runes.RunePage.from_opgg(RAW_RUNE)
    assert page.primary_style == 8000
    assert page.sub_style == 8300
    assert page.play == 1000


def test_runepage_selected_perk_ids_order():
    page = opgg_runes.RunePage.from_opgg(RAW_RUNE)
    # 4 primary + 2 secondary + 3 shards, in that order.
    assert page.selected_perk_ids == [8005, 9101, 9104, 8014, 8313, 8321, 5005, 5008, 5011]


def test_runepage_winrate():
    assert opgg_runes.RunePage.from_opgg(RAW_RUNE).winrate == 0.52
    assert opgg_runes.RunePage.from_opgg({**RAW_RUNE, "play": 0, "win": 0}).winrate == 0.0


def test_runepage_to_lcu_page():
    page = opgg_runes.RunePage.from_opgg(RAW_RUNE)
    assert page.to_lcu_page("AUTO - Jinx adc") == {
        "name": "AUTO - Jinx adc",
        "primaryStyleId": 8000,
        "subStyleId": 8300,
        "selectedPerkIds": [8005, 9101, 9104, 8014, 8313, 8321, 5005, 5008, 5011],
        "current": True,
    }


# --- fetch_build / best_build (network stubbed) ----------------------------- #
def test_fetch_build_sorts_runes_and_spells(monkeypatch):
    payload = {
        "data": {
            "runes": [RAW_RUNE, {**RAW_RUNE, "play": 5000}],
            "summoner_spells": [
                {"ids": [4, 14], "play": 10},
                {"ids": [4, 11], "play": 900},
            ],
        }
    }
    monkeypatch.setattr(opgg_runes, "get_json", lambda url: payload)
    build = opgg_runes.fetch_build(64, "jungle")
    assert build.position == "jungle"
    assert build.best_runes.play == 5000  # most-played rune page first
    assert build.best_spells == [4, 11]  # most-played spell pair first


def test_fetch_build_returns_none_without_runes(monkeypatch):
    monkeypatch.setattr(opgg_runes, "get_json", lambda url: {"data": {"runes": []}})
    assert opgg_runes.fetch_build(64, "jungle") is None


def test_best_build_aram_is_positionless(monkeypatch):
    seen = {}

    def fake_get_json(url):
        seen["url"] = url
        return {"data": {"runes": [RAW_RUNE], "summoner_spells": [{"ids": [4, 32], "play": 1}]}}

    monkeypatch.setattr(opgg_runes, "get_json", fake_get_json)
    build = opgg_runes.best_build(64, mode="aram")
    assert build.position == "aram"
    assert build.best_spells == [4, 32]
    assert seen["url"].endswith("/aram/64/none")
