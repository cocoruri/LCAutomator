"""Tests for lcu_watch: pure helpers plus the draft/queue logic.

Async functions are driven with the `run` fixture (asyncio.run) against the
FakeConnection from conftest, so no live client or pytest-asyncio is needed.
"""

import pytest

import lcu_watch
from conftest import FakeConnection, FakeResponse


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status,expected",
    [(200, True), (201, True), (204, True), (199, False), (300, False), (400, False), (500, False)],
)
def test_ok(status, expected):
    assert lcu_watch.ok(status) is expected


@pytest.mark.parametrize(
    "game_mode,expected",
    [("ARAM", "aram"), ("aram", "aram"), ("CLASSIC", "ranked"),
     ("KIWI", None), ("CHERRY", None), ("URF", None), ("", None)],
)
def test_opgg_mode_for(game_mode, expected):
    assert lcu_watch.opgg_mode_for(game_mode) == expected


@pytest.mark.parametrize(
    "ids,expected",
    [
        ([11, 4], [4, 11]),     # Smite, Flash -> Flash on D, Smite on F
        ([4, 11], [4, 11]),
        ([11, 6], [6, 11]),     # Ghost forced to D
        ([14, 4], [4, 14]),     # Flash to D, Ignite unconstrained -> F
        ([12, 4], [4, 12]),     # Flash to D, Teleport stays
        ([7, 3], [7, 3]),       # neither constrained -> order preserved
        ([4, 6], [4, 6]),       # both want D -> stable, Flash keeps D
    ],
)
def test_arrange_spells(ids, expected):
    assert lcu_watch.arrange_spells(ids) == expected


def test_queue_label():
    assert lcu_watch.queue_label(440) == "Flex"
    assert lcu_watch.queue_label(2400) == "ARAM Mayhem"
    assert lcu_watch.queue_label(999, "SOMEMODE") == "SOMEMODE"  # unknown id -> gameMode
    assert lcu_watch.queue_label(999) == "queue 999"  # nothing known


def test_unavailable_champions():
    session = {
        "bans": {"myTeamBans": [1], "theirTeamBans": [2]},
        "actions": [[
            {"type": "ban", "championId": 3, "completed": True},
            {"type": "pick", "championId": 4, "completed": True},
            {"type": "pick", "championId": 99, "completed": False},  # in progress, not taken
        ]],
        "myTeam": [{"championId": 5}],
        "theirTeam": [{"championId": 0}],
    }
    banned, taken = lcu_watch.unavailable_champions(session)
    assert banned == {1, 2, 3}
    assert taken == {4, 5}
    assert 99 not in taken


def test_local_pick():
    session = {
        "localPlayerCellId": 2,
        "actions": [[
            {"actorCellId": 1, "type": "pick", "championId": 10, "completed": True},
            {"actorCellId": 2, "type": "pick", "championId": 20, "completed": False},
        ]],
    }
    assert lcu_watch.local_pick(session) == (20, False)
    session["actions"][0][1]["completed"] = True
    assert lcu_watch.local_pick(session) == (20, True)


def test_local_action_in_progress():
    session = {
        "localPlayerCellId": 2,
        "actions": [[
            {"id": 7, "actorCellId": 2, "type": "ban", "isInProgress": True, "completed": False},
            {"id": 8, "actorCellId": 2, "type": "pick", "isInProgress": False, "completed": False},
        ]],
    }
    assert lcu_watch.local_action_in_progress(session, "ban")["id"] == 7
    assert lcu_watch.local_action_in_progress(session, "pick") is None


def test_assigned_lane():
    session = {"localPlayerCellId": 2, "myTeam": [{"cellId": 2, "assignedPosition": "middle"}]}
    assert lcu_watch.assigned_lane(session) == "MIDDLE"
    assert lcu_watch.assigned_lane({"localPlayerCellId": 9, "myTeam": []}) == ""


# --------------------------------------------------------------------------- #
# CLI -> Autopilot
# --------------------------------------------------------------------------- #
def _fake_resolve(name):
    table = {"shaco": (35, "Shaco"), "briar": (233, "Briar"),
             "leesin": (64, "Lee Sin"), "zed": (238, "Zed")}
    key = "".join(c for c in name.lower() if c.isalnum())
    cid, disp = table[key]
    return {"id": cid, "name": disp}


def test_parse_args_repeated_lanes():
    args = lcu_watch.parse_args(
        ["--mode", "solo", "--lane", "mid", "Ahri", "Lux", "--lane", "top", "Garen", "Sett"]
    )
    assert args.mode == "solo"
    assert args.lane == [["mid", "Ahri", "Lux"], ["top", "Garen", "Sett"]]
    assert args.ban is None
    assert args.no_start is False


def test_build_autopilot(monkeypatch):
    monkeypatch.setattr(lcu_watch.opgg_runes, "resolve_champion", _fake_resolve)
    args = lcu_watch.parse_args(
        ["--mode", "flex", "--lane", "jungle", "Shaco", "Briar", "--ban", "Lee Sin", "Zed", "--no-start"]
    )
    ap = lcu_watch.build_autopilot(args)
    assert ap.mode == "flex"
    assert ap.lane_order == ["JUNGLE"]
    assert ap.lanes == {"JUNGLE": [35, 233]}
    assert ap.bans == [64, 238]
    assert ap.start is False
    assert ap.is_aram is False


# --------------------------------------------------------------------------- #
# Draft automation (async)
# --------------------------------------------------------------------------- #
def _arm(lanes=None, bans=None, mode="flex"):
    lanes = lanes or {"JUNGLE": [35, 233]}
    bans = bans if bans is not None else [64, 238]
    lcu_watch.AUTOPILOT = lcu_watch.Autopilot(
        mode=mode, lanes=lanes, lane_order=list(lanes), bans=bans
    )


def _ban_session(phase="BAN_PICK", banned=()):
    return {
        "localPlayerCellId": 2,
        "timer": {"phase": phase},
        "myTeam": [{"cellId": 2, "assignedPosition": "jungle", "championId": 0}],
        "theirTeam": [],
        "bans": {"myTeamBans": [], "theirTeamBans": list(banned)},
        "actions": [[{"id": 10, "actorCellId": 2, "type": "ban", "isInProgress": True, "completed": False}]],
    }


def _pick_session(phase="BAN_PICK", lane="jungle", banned=(), taken=()):
    return {
        "localPlayerCellId": 2,
        "timer": {"phase": phase},
        "myTeam": [{"cellId": 2, "assignedPosition": lane, "championId": 0}],
        "theirTeam": [{"championId": c} for c in taken],
        "bans": {"myTeamBans": [], "theirTeamBans": list(banned)},
        "actions": [[{"id": 20, "actorCellId": 2, "type": "pick", "isInProgress": True, "completed": False}]],
    }


def test_draft_bans_first_choice(run):
    _arm()
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _ban_session()))
    assert conn.patches() == [("/lol-champ-select/v1/session/actions/10", {"championId": 64, "completed": True})]


def test_draft_bans_second_when_first_already_banned(run):
    _arm()
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _ban_session(banned=[64])))
    assert conn.patches() == [("/lol-champ-select/v1/session/actions/10", {"championId": 238, "completed": True})]


def test_draft_skips_ban_when_both_banned(run):
    _arm()
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _ban_session(banned=[64, 238])))
    assert conn.patches() == []


def test_draft_waits_for_ban_pick_phase(run):
    _arm()
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _ban_session(phase="PLANNING")))
    assert conn.patches() == []  # nothing during the planning phase


def test_draft_picks_first_choice(run):
    _arm()
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _pick_session()))
    assert conn.patches() == [("/lol-champ-select/v1/session/actions/20", {"championId": 35, "completed": True})]


def test_draft_picks_second_when_first_taken(run):
    _arm()
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _pick_session(taken=[35])))
    assert conn.patches() == [("/lol-champ-select/v1/session/actions/20", {"championId": 233, "completed": True})]


def test_draft_leaves_pick_when_both_unavailable(run):
    _arm()
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _pick_session(banned=[35], taken=[233])))
    assert conn.patches() == []


def test_draft_leaves_pick_on_unconfigured_lane(run):
    _arm()  # only JUNGLE configured
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _pick_session(lane="top")))
    assert conn.patches() == []


def test_draft_does_nothing_in_aram(run):
    _arm(mode="aram")
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _ban_session()))
    assert conn.patches() == []


# --------------------------------------------------------------------------- #
# Queue setup (async)
# --------------------------------------------------------------------------- #
def test_queue_candidates_ranked(run):
    assert run(lcu_watch.queue_candidates(FakeConnection(), "solo")) == [420]
    assert run(lcu_watch.queue_candidates(FakeConnection(), "flex")) == [440]


def test_queue_candidates_aram_prefers_mayhem_then_fallback(run):
    def handler(method, endpoint, body):
        if "game-queues" in endpoint:
            return FakeResponse(200, [
                {"id": 2400, "name": "ARAM Mayhem", "description": "", "queueAvailability": "Available"},
                {"id": 450, "name": "ARAM", "description": "", "queueAvailability": "Available"},
            ])
        return None

    assert run(lcu_watch.queue_candidates(FakeConnection(handler), "aram")) == [2400, 450]


def test_set_role_prefs_full_stack_single_role(run):
    _arm(lanes={"JUNGLE": [35, 233], "MIDDLE": [103, 99]})

    def handler(method, endpoint, body):
        if endpoint == "/lol-lobby/v2/lobby" and method == "get":
            return FakeResponse(200, {"members": [{}] * 5})
        return None

    conn = FakeConnection(handler)
    run(lcu_watch.set_role_prefs(conn, lcu_watch.AUTOPILOT))
    assert conn.puts() == [{"firstPreference": "JUNGLE", "secondPreference": "UNSELECTED"}]


def test_set_role_prefs_small_party_two_prefs(run):
    _arm(lanes={"JUNGLE": [35, 233], "MIDDLE": [103, 99]})

    def handler(method, endpoint, body):
        if endpoint == "/lol-lobby/v2/lobby" and method == "get":
            return FakeResponse(200, {"members": [{}] * 3})
        return None

    conn = FakeConnection(handler)
    run(lcu_watch.set_role_prefs(conn, lcu_watch.AUTOPILOT))
    assert conn.puts() == [{"firstPreference": "JUNGLE", "secondPreference": "MIDDLE"}]


# --------------------------------------------------------------------------- #
# Ready check (async)
# --------------------------------------------------------------------------- #
def test_ready_check_accepts_when_pending(run):
    conn = FakeConnection()
    run(lcu_watch.maybe_accept_ready_check(conn, {"state": "InProgress", "playerResponse": "None"}))
    assert "/lol-matchmaking/v1/ready-check/accept" in conn.posts()


def test_ready_check_ignores_already_accepted(run):
    conn = FakeConnection()
    run(lcu_watch.maybe_accept_ready_check(conn, {"state": "InProgress", "playerResponse": "Accepted"}))
    assert conn.posts() == []
