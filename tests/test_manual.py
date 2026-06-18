"""Tests for the GUI champion search + pre-configuration helpers (headless, no Qt).

The GUI no longer picks/bans immediately; these helpers only resolve/search
champion names so the user can pre-configure the autopilot. See
test_autopilot_builder.py for the config->Autopilot wiring.
"""

from __future__ import annotations

import pytest

from src import manual
from src.manual import (
    ChampionNotFoundError,
    resolve_champion,
    search_champions,
)


class _FakeOpgg:
    """Minimal stand-in for the opgg_runes module."""

    INDEX = {
        "ahri": {"id": 103, "name": "Ahri"},
        "garen": {"id": 86, "name": "Garen"},
        "ashe": {"id": 22, "name": "Ashe"},
    }

    def champion_index(self):
        return self.INDEX

    def resolve_champion(self, name):
        for entry in self.INDEX.values():
            if entry["name"].lower() == name.lower():
                return entry
        raise ValueError(f"Unknown champion: {name!r}")


@pytest.fixture
def fake_opgg(monkeypatch):
    fake = _FakeOpgg()
    monkeypatch.setattr(manual, "opgg_runes", fake)  # the binding manual.* reads
    return fake


# --- search ----------------------------------------------------------------- #
def test_search_filters_by_substring(fake_opgg):
    assert search_champions("ah") == [(103, "Ahri")]


def test_search_is_case_insensitive_and_sorted(fake_opgg):
    # "a" matches Ahri, Ashe, Garen; result is sorted by display name.
    assert search_champions("A") == [(103, "Ahri"), (22, "Ashe"), (86, "Garen")]


def test_search_empty_returns_all_sorted(fake_opgg):
    assert [name for _, name in search_champions("")] == ["Ahri", "Ashe", "Garen"]


def test_search_no_match_is_empty_not_error(fake_opgg):
    assert search_champions("zzz") == []


def test_search_without_opgg_is_empty(monkeypatch):
    monkeypatch.setattr(manual, "opgg_runes", None)
    assert search_champions("ahri") == []


# --- resolve ---------------------------------------------------------------- #
def test_resolve_returns_id_and_name(fake_opgg):
    assert resolve_champion("garen") == (86, "Garen")


def test_resolve_unknown_raises(fake_opgg):
    with pytest.raises(ChampionNotFoundError):
        resolve_champion("nope")


def test_resolve_blank_raises(fake_opgg):
    with pytest.raises(ChampionNotFoundError):
        resolve_champion("   ")
