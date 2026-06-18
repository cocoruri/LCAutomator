"""Tests for the pure build-mapping logic in opgg_runes."""

import json
import urllib.error

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


@pytest.fixture(autouse=True)
def _reset_opgg_caches():
    """Isolate the module-level reference-data caches between tests."""
    opgg_runes._champion_index = None
    opgg_runes._perk_names = None
    opgg_runes._champion_meta = None
    yield


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


# --- best_build ranked lane selection + fallback [TO_FIX #2] ---------------- #
def _page(play):
    return opgg_runes.RunePage(
        primary_style=8000, sub_style=8300, primary_rune_ids=[],
        secondary_rune_ids=[], stat_mod_ids=[], play=play,
    )


def _build(position, play=100):
    return opgg_runes.Build(position=position, runes=[_page(play)], spells=[[4, 11]])


def test_best_build_prefers_given_lane(monkeypatch):
    monkeypatch.setattr(
        opgg_runes, "fetch_build",
        lambda cid, pos, region="global", mode="ranked": _build(pos),
    )
    assert opgg_runes.best_build(64, preferred="mid").position == "mid"


def test_best_build_falls_back_to_champion_positions(monkeypatch):
    monkeypatch.setattr(opgg_runes, "champion_positions", lambda cid: ["jungle", "top"])
    builds = {"jungle": _build("jungle")}
    monkeypatch.setattr(
        opgg_runes, "fetch_build",
        lambda cid, pos, region="global", mode="ranked": builds.get(pos),
    )
    # 'mid' misses -> use champion_positions order -> jungle
    assert opgg_runes.best_build(64, preferred="mid").position == "jungle"


def test_best_build_probes_all_positions_keeps_most_played(monkeypatch):
    monkeypatch.setattr(opgg_runes, "champion_positions", lambda cid: [])
    builds = {"top": _build("top", play=10), "adc": _build("adc", play=999)}
    monkeypatch.setattr(
        opgg_runes, "fetch_build",
        lambda cid, pos, region="global", mode="ranked": builds.get(pos),
    )
    assert opgg_runes.best_build(64).position == "adc"


def test_best_build_raises_when_no_build(monkeypatch):
    monkeypatch.setattr(opgg_runes, "champion_positions", lambda cid: [])
    monkeypatch.setattr(
        opgg_runes, "fetch_build",
        lambda cid, pos, region="global", mode="ranked": None,
    )
    with pytest.raises(RuntimeError):
        opgg_runes.best_build(64)


def test_best_build_aram_raises_when_none(monkeypatch):
    monkeypatch.setattr(
        opgg_runes, "fetch_build",
        lambda cid, pos, region="global", mode="ranked": None,
    )
    with pytest.raises(RuntimeError):
        opgg_runes.best_build(64, mode="aram")


# --- fetch_build HTTP error branching [TO_FIX #4] --------------------------- #
def _raise_http(code):
    def get_json(url):
        raise urllib.error.HTTPError(url, code, "err", {}, None)

    return get_json


@pytest.mark.parametrize("code", [404, 422])
def test_fetch_build_not_found_returns_none(monkeypatch, code):
    monkeypatch.setattr(opgg_runes, "get_json", _raise_http(code))
    assert opgg_runes.fetch_build(64, "jungle") is None


def test_fetch_build_other_http_error_reraises(monkeypatch):
    monkeypatch.setattr(opgg_runes, "get_json", _raise_http(500))
    with pytest.raises(urllib.error.HTTPError):
        opgg_runes.fetch_build(64, "jungle")


def test_fetch_build_empty_spells(monkeypatch):
    monkeypatch.setattr(
        opgg_runes, "get_json",
        lambda url: {"data": {"runes": [RAW_RUNE], "summoner_spells": []}},
    )
    assert opgg_runes.fetch_build(64, "jungle").best_spells == []


def test_fetch_build_positionless_label_is_mode(monkeypatch):
    monkeypatch.setattr(
        opgg_runes, "get_json",
        lambda url: {"data": {"runes": [RAW_RUNE], "summoner_spells": []}},
    )
    assert opgg_runes.fetch_build(64, "none", mode="aram").position == "aram"


# --- reference-data caching [TO_FIX #11] ------------------------------------ #
def test_cache_round_trip(tmp_path):
    path = str(tmp_path / "x.json")
    opgg_runes._save_cache(path, {"a": 1})
    assert opgg_runes._load_cache(path) == {"a": 1}


def test_load_cache_missing_returns_none(tmp_path):
    assert opgg_runes._load_cache(str(tmp_path / "nope.json")) is None


def test_load_cache_tolerates_unreadable(tmp_path):
    a_dir = tmp_path / "adir"
    a_dir.mkdir()
    assert opgg_runes._load_cache(str(a_dir)) is None  # opening a dir -> OSError -> None


def test_save_cache_tolerates_oserror(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise OSError("no")

    monkeypatch.setattr(opgg_runes.os, "makedirs", boom)
    opgg_runes._save_cache(str(tmp_path / "x.json"), {"a": 1})  # must not raise


def test_champion_index_prefers_cache(monkeypatch):
    monkeypatch.setattr(opgg_runes, "_load_cache", lambda path: {"cached": {"id": 1, "name": "X"}})
    fetched = {"called": False}

    def fetch():
        fetched["called"] = True
        return {}

    monkeypatch.setattr(opgg_runes, "_fetch_champion_index", fetch)
    assert opgg_runes.champion_index() == {"cached": {"id": 1, "name": "X"}}
    assert fetched["called"] is False  # served from cache, no network


def test_champion_index_falls_back_to_network(monkeypatch):
    monkeypatch.setattr(opgg_runes, "_load_cache", lambda path: None)
    saved = {}
    monkeypatch.setattr(opgg_runes, "_save_cache", lambda path, data: saved.update(data=data))
    monkeypatch.setattr(opgg_runes, "_fetch_champion_index", lambda: {"net": {"id": 2, "name": "Y"}})
    idx = opgg_runes.champion_index()
    assert idx == {"net": {"id": 2, "name": "Y"}}
    assert saved["data"] == idx  # fetched result is cached


def test_perk_names_restores_int_keys(monkeypatch):
    monkeypatch.setattr(opgg_runes, "_load_cache", lambda path: {"8000": "Domination", "5008": "Adaptive"})
    names = opgg_runes.perk_names()
    assert names[8000] == "Domination"  # JSON string keys restored to int
    assert names[5008] == "Adaptive"


def test_perk_name_refreshes_on_miss(monkeypatch):
    calls = {"n": 0}

    def fake_perk_names(refresh=False):
        calls["n"] += 1
        return {8000: "Domination"} if refresh else {}

    monkeypatch.setattr(opgg_runes, "perk_names", fake_perk_names)
    assert opgg_runes.perk_name(8000) == "Domination"
    assert calls["n"] == 2  # initial miss + one refresh


# --- opgg_runes.main() CLI [TO_FIX #13] ------------------------------------- #
def test_main_unknown_champion_exits_1(monkeypatch, capsys):
    def boom(name):
        raise ValueError("Unknown champion: 'Zzz'")

    monkeypatch.setattr(opgg_runes, "resolve_champion", boom)
    assert opgg_runes.main(["Zzz"]) == 1
    assert "unknown champion" in capsys.readouterr().err.lower()


def test_main_no_rune_data_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(opgg_runes, "resolve_champion", lambda n: {"id": 222, "name": "Jinx"})
    monkeypatch.setattr(opgg_runes, "fetch_runes", lambda cid, pos, region="global", mode="ranked": [])
    assert opgg_runes.main(["Jinx", "--position", "adc"]) == 1
    assert "no rune data" in capsys.readouterr().err.lower()


def test_main_json_single_page(monkeypatch, capsys):
    monkeypatch.setattr(opgg_runes, "resolve_champion", lambda n: {"id": 222, "name": "Jinx"})
    page = opgg_runes.RunePage.from_opgg(RAW_RUNE)
    monkeypatch.setattr(opgg_runes, "best_position", lambda cid, region, mode: ("adc", [page]))
    assert opgg_runes.main(["Jinx", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["primaryStyleId"] == 8000
    assert body["selectedPerkIds"] == page.selected_perk_ids


def test_main_json_all_outputs_list(monkeypatch, capsys):
    monkeypatch.setattr(opgg_runes, "resolve_champion", lambda n: {"id": 222, "name": "Jinx"})
    page = opgg_runes.RunePage.from_opgg(RAW_RUNE)
    monkeypatch.setattr(opgg_runes, "best_position", lambda cid, region, mode: ("adc", [page, page]))
    assert opgg_runes.main(["Jinx", "--json", "--all"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert isinstance(body, list) and len(body) == 2


def test_main_explicit_position_used(monkeypatch):
    monkeypatch.setattr(opgg_runes, "resolve_champion", lambda n: {"id": 222, "name": "Jinx"})
    seen = {}

    def fake_fetch_runes(cid, pos, region="global", mode="ranked"):
        seen["pos"] = pos
        return [opgg_runes.RunePage.from_opgg(RAW_RUNE)]

    monkeypatch.setattr(opgg_runes, "fetch_runes", fake_fetch_runes)
    assert opgg_runes.main(["Jinx", "--position", "mid", "--json"]) == 0
    assert seen["pos"] == "mid"
