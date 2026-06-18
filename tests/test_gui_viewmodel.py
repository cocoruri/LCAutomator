"""Tests for gui.viewmodel — the Qt-free GUI render transforms.

Importing gui.viewmodel must not require a display (it imports only src.events),
so these run headless in CI alongside the rest of the suite.
"""

from __future__ import annotations

from gui import viewmodel
from src.events import ChampSelectView, PlayerView, SummonerInfo


def test_summoner_text_with_level():
    assert viewmodel.summoner_text(SummonerInfo("Reze", 42)) == "Reze - level 42"


def test_summoner_text_none():
    assert viewmodel.summoner_text(None) == "Not logged in"


def test_phase_text_known_and_unknown():
    assert viewmodel.phase_text("ChampSelect") == "Phase: ChampSelect"
    assert viewmodel.phase_text(None) == "Phase: Unknown"


def test_player_row_locked_and_me():
    row = viewmodel.player_row(PlayerView("mid", "Ahri", locked=True, is_me=True))
    assert "Ahri (locked)" in row and "<- you" in row


def test_player_row_hovering_no_champ():
    assert "-" in viewmodel.player_row(PlayerView("top", None, locked=False, is_me=False))


def test_champ_select_lines_sections():
    view = ChampSelectView(
        your_pick="Ahri",
        your_pick_locked=True,
        my_team=(PlayerView("mid", "Ahri", True, True),),
        enemy_champions=("Zed",),
        my_bans=("Yasuo",),
        their_bans=("Teemo",),
    )
    lines = viewmodel.champ_select_lines(view)
    assert lines["summary"] == ["Your pick: Ahri (LOCKED IN)"]
    assert lines["enemy"] == ["Zed"]
    assert lines["my_bans"] == ["Yasuo"] and lines["their_bans"] == ["Teemo"]
    assert len(lines["team"]) == 1


def test_champ_select_lines_no_pick():
    view = ChampSelectView(your_pick=None, your_pick_locked=False)
    assert viewmodel.champ_select_lines(view)["summary"] == ["Your pick: (none yet)"]


# --- autopilot config transform --------------------------------------------- #
def test_autopilot_lanes_drops_empty_and_keeps_order():
    lane_choices = {
        "top": [(86, "Garen")],
        "jungle": [],  # empty -> dropped
        "mid": [(103, "Ahri"), (238, "Zed")],
    }
    assert viewmodel.autopilot_lanes(lane_choices) == [
        ("top", ["Garen"]),
        ("mid", ["Ahri", "Zed"]),
    ]


def test_autopilot_lanes_empty_config_is_empty_list():
    assert viewmodel.autopilot_lanes({"top": [], "mid": []}) == []
