"""Tests for the framework-agnostic event sink + the champ-select view transform.

These exercise the decoupling layer the GUI sits on, with no Qt/display needed.
"""

from __future__ import annotations

import lcu_watch
from src import events
from src.display import build_champ_select_view
from src.events import (
    ChampSelectUpdate,
    ConnectedUpdate,
    NoticeUpdate,
    PhaseUpdate,
    RecordingSink,
    reset_sink,
    set_sink,
)
from src.state import _champ_names


def test_recording_sink_collects_in_order():
    sink = RecordingSink()
    set_sink(sink)
    try:
        events.emit(ConnectedUpdate(summoner=None))
        events.emit(PhaseUpdate(phase="Lobby"))
    finally:
        reset_sink()
    assert sink.updates == [ConnectedUpdate(None), PhaseUpdate("Lobby")]


def test_emit_swallows_sink_errors(capsys):
    class Boom:
        def emit(self, update):
            raise RuntimeError("kaboom")

    set_sink(Boom())
    try:
        events.emit(NoticeUpdate(text="hi"))  # must not raise
    finally:
        reset_sink()
    assert "event sink raised" in capsys.readouterr().err.lower()


def test_default_sink_is_noop():
    reset_sink()
    events.emit(PhaseUpdate(phase="None"))  # NullSink drops it, no error


def test_on_phase_emits_phase_update(run):
    sink = RecordingSink()
    set_sink(sink)
    try:
        run(lcu_watch.on_phase(None, _Event("ChampSelect")))
    finally:
        reset_sink()
    assert PhaseUpdate("ChampSelect") in sink.updates


class _Event:
    def __init__(self, data):
        self.data = data


# --- build_champ_select_view ------------------------------------------------ #
def _session():
    cell = 0
    return {
        "localPlayerCellId": cell,
        "actions": [[{"actorCellId": cell, "type": "pick", "championId": 1, "completed": True}]],
        "myTeam": [
            {"cellId": 0, "assignedPosition": "middle", "championId": 1},
            {"cellId": 1, "assignedPosition": "top", "championPickIntent": 2},
        ],
        "theirTeam": [{"championId": 3}, {"championId": 0}],
        "bans": {"myTeamBans": [4, 0], "theirTeamBans": [5]},
    }


def test_build_view_resolves_names_and_lock_state():
    _champ_names.update({1: "Ahri", 2: "Garen", 3: "Zed", 4: "Yasuo", 5: "Teemo"})
    view = build_champ_select_view(_session())

    assert view.your_pick == "Ahri"
    assert view.your_pick_locked is True
    # my_team sorted by position order (top before mid in LCU_TO_OPGG)
    positions = [p.position for p in view.my_team]
    assert positions == ["top", "mid"]
    top, mid = view.my_team
    assert top.champion == "Garen" and top.locked is False  # hovering
    assert mid.champion == "Ahri" and mid.locked is True and mid.is_me is True
    assert view.enemy_champions == ("Zed",)  # the 0-id enemy is dropped
    assert view.my_bans == ("Yasuo",) and view.their_bans == ("Teemo",)


def test_build_view_handles_empty_session():
    view = build_champ_select_view({})
    assert view.your_pick is None
    assert view.my_team == () and view.enemy_champions == ()
    assert view.my_bans == () and view.their_bans == ()


def test_on_champ_select_emits_view(run, monkeypatch):
    from src import handlers

    monkeypatch.setattr(lcu_watch, "AUTO_APPLY", False)  # don't trigger apply_build
    _champ_names.update({1: "Ahri"})
    sink = RecordingSink()
    set_sink(sink)
    try:
        run(handlers.on_champ_select(None, _Event(_session())))
    finally:
        reset_sink()
    views = [u for u in sink.updates if isinstance(u, ChampSelectUpdate)]
    assert len(views) == 1 and views[0].view.your_pick == "Ahri"
