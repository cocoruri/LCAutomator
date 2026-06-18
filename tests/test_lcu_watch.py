"""Tests for lcu_watch: pure helpers plus the draft/queue logic.

Async functions are driven with the `run` fixture (asyncio.run) against the
FakeConnection from conftest, so no live client or pytest-asyncio is needed.
"""

import pytest

import lcu_watch
import opgg_runes
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
    assert lcu_watch.queue_label(lcu_watch.QUEUE_FLEX) == "Flex"
    assert lcu_watch.queue_label(lcu_watch.QUEUE_ARAM_MAYHEM) == "ARAM Mayhem"
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


@pytest.mark.parametrize(
    "position,label",
    [("utility", "support"), ("bottom", "adc"), ("middle", "mid"), ("top", "top"), ("", "?")],
)
def test_player_line_lane_label(position, label):
    line = lcu_watch.player_line({"assignedPosition": position, "championId": 0}, is_me=False)
    assert label in line


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


def test_is_aram_session_reads_bench_flag():
    assert lcu_watch.is_aram_session({"benchEnabled": True}) is True
    assert lcu_watch.is_aram_session({"benchEnabled": False}) is False
    assert lcu_watch.is_aram_session({}) is False  # draft session: no bench


def test_draft_skips_aram_session_regardless_of_mode(run):
    # The gate is the *session* (benchEnabled = ARAM), not the configured mode:
    # even a flex-mode autopilot must not draft an ARAM bench session.
    _arm(mode="flex")
    conn = FakeConnection()
    session = _ban_session()
    session["benchEnabled"] = True
    run(lcu_watch.run_draft(conn, session))
    assert conn.patches() == []


def test_draft_runs_for_draft_session_even_when_watch_only(run):
    # Watch-only (start=False) and a queue we didn't pick via --mode must still
    # auto-draft a normal draft session: the gate is the session, not start/mode.
    lcu_watch.AUTOPILOT = lcu_watch.Autopilot(
        mode="solo", lanes={"JUNGLE": [35, 233]}, lane_order=["JUNGLE"],
        bans=[64, 238], start=False,
    )
    conn = FakeConnection()
    run(lcu_watch.run_draft(conn, _ban_session()))  # no benchEnabled -> drafts
    assert conn.patches() == [
        ("/lol-champ-select/v1/session/actions/10", {"championId": 64, "completed": True})
    ]


# --------------------------------------------------------------------------- #
# Queue setup (async)
# --------------------------------------------------------------------------- #
def _queues_handler(*queue_ids_available):
    """Handler that reports the given queue ids as Available, all others absent."""
    available = {qid for qid in queue_ids_available}
    def handler(method, endpoint, body):
        if "game-queues" in endpoint:
            return FakeResponse(200, [
                {"id": qid, "queueAvailability": "Available"} for qid in available
            ])
        return None
    return handler


def test_fetch_available_queues_filters_disabled(run):
    def handler(method, endpoint, body):
        if "game-queues" in endpoint:
            return FakeResponse(200, [
                {"id": 420, "queueAvailability": "Available"},
                {"id": 440, "queueAvailability": "PlatformDisabled"},
                {"id": 450, "queueAvailability": "Available"},
            ])
        return None
    result = run(lcu_watch.fetch_available_queues(FakeConnection(handler)))
    assert [q["id"] for q in result] == [420, 450]


def test_queue_candidates_ranked(run):
    solo_conn = FakeConnection(_queues_handler(lcu_watch.QUEUE_SOLO))
    flex_conn = FakeConnection(_queues_handler(lcu_watch.QUEUE_FLEX))
    assert run(lcu_watch.queue_candidates(solo_conn, "solo")) == [lcu_watch.QUEUE_SOLO]
    assert run(lcu_watch.queue_candidates(flex_conn, "flex")) == [lcu_watch.QUEUE_FLEX]


def test_queue_candidates_ranked_disabled_returns_empty(run):
    conn = FakeConnection(_queues_handler())  # no queues available
    assert run(lcu_watch.queue_candidates(conn, "solo")) == []


def test_queue_candidates_aram_both_available(run):
    conn = FakeConnection(_queues_handler(lcu_watch.QUEUE_ARAM_MAYHEM, lcu_watch.QUEUE_ARAM))
    # Mayhem comes first (MODE_QUEUES order), plain ARAM as fallback.
    assert run(lcu_watch.queue_candidates(conn, "aram")) == [
        lcu_watch.QUEUE_ARAM_MAYHEM, lcu_watch.QUEUE_ARAM
    ]


def test_queue_candidates_aram_mayhem_disabled(run):
    # When Mayhem is not in the available set, only plain ARAM is returned.
    conn = FakeConnection(_queues_handler(lcu_watch.QUEUE_ARAM))
    assert run(lcu_watch.queue_candidates(conn, "aram")) == [lcu_watch.QUEUE_ARAM]


def test_set_role_prefs_full_stack_single_role(run):
    _arm(lanes={"JUNGLE": [35, 233], "MIDDLE": [103, 99]})

    conn = FakeConnection(_members_handler(5))
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


def test_stop_queue_deletes_search(run):
    calls = []

    def handler(method, endpoint, body):
        calls.append((method, endpoint))
        return FakeResponse(204)

    assert run(lcu_watch.stop_queue(FakeConnection(handler))) is True
    assert ("delete", "/lol-lobby/v2/lobby/matchmaking/search") in calls


def test_stop_queue_false_when_nothing_to_cancel(run):
    conn = FakeConnection(lambda m, e, b: FakeResponse(404))
    assert run(lcu_watch.stop_queue(conn)) is False  # reported, not raised


def test_ready_check_respects_runtime_auto_accept_toggle(run, monkeypatch):
    # The toggle must take effect through the shim at call time (read live).
    monkeypatch.setattr(lcu_watch, "AUTO_ACCEPT", False)
    conn = FakeConnection()
    run(lcu_watch.maybe_accept_ready_check(conn, {"state": "InProgress", "playerResponse": "None"}))
    assert conn.posts() == []  # AUTO_ACCEPT off -> no accept


# --------------------------------------------------------------------------- #
# set_runes: rune-page management (mutates the client)  [TO_FIX #1]
# --------------------------------------------------------------------------- #
PAGE_BODY = {
    "name": "AUTO - Jinx adc",
    "primaryStyleId": 8000,
    "subStyleId": 8300,
    "selectedPerkIds": [1, 2, 3],
    "current": True,
}


def _perks_handler(pages, put_status=200):
    """/lol-perks handler for the edit-in-place flow: GET pages, PUT pages/{id}."""

    def handler(method, endpoint, body):
        if endpoint == "/lol-perks/v1/pages" and method == "get":
            return FakeResponse(200, [dict(p) for p in pages])
        if endpoint.startswith("/lol-perks/v1/pages/") and method == "put":
            return FakeResponse(put_status)
        return None

    return handler


def _puts(conn):
    return [(e, b) for (m, e, b) in conn.calls if m == "put"]


def test_set_runes_edits_the_active_page(run):
    conn = FakeConnection(_perks_handler(
        [{"id": 1, "name": "My Page", "isEditable": True, "current": False},
         {"id": 2, "name": "Scratch", "isEditable": True, "current": True}]))
    run(lcu_watch.set_runes(conn, PAGE_BODY))
    # The build is PUT onto the currently-selected page; no create, no slot juggling.
    assert _puts(conn) == [("/lol-perks/v1/pages/2", PAGE_BODY)]
    assert conn.posts() == []


def test_set_runes_warns_when_no_active_page(run, capsys):
    conn = FakeConnection(_perks_handler(
        [{"id": 1, "name": "My Page", "isEditable": True, "current": False}]))
    run(lcu_watch.set_runes(conn, PAGE_BODY))
    assert _puts(conn) == []  # nothing edited
    assert "no active rune page" in capsys.readouterr().out.lower()


def test_set_runes_skips_uneditable_active_page(run, capsys):
    conn = FakeConnection(_perks_handler(
        [{"id": 9, "name": "Recommended", "isEditable": False, "current": True}]))
    run(lcu_watch.set_runes(conn, PAGE_BODY))
    assert _puts(conn) == []  # never edit a page the client locks
    assert "can't be edited" in capsys.readouterr().out.lower()


def test_set_runes_warns_on_failed_put(run, capsys):
    conn = FakeConnection(_perks_handler(
        [{"id": 2, "name": "Scratch", "isEditable": True, "current": True}],
        put_status=400))
    run(lcu_watch.set_runes(conn, PAGE_BODY))
    assert "failed" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------- #
# attempt_action: retry / give-up  [TO_FIX #3]
# --------------------------------------------------------------------------- #
def _patch_status_handler(statuses):
    seq = iter(statuses)

    def handler(method, endpoint, body):
        if "/actions/" in endpoint and method == "patch":
            return FakeResponse(next(seq, 200))
        return None

    return handler


def test_attempt_action_success_marks_handled(run):
    conn = FakeConnection(_patch_status_handler([200]))
    run(lcu_watch.attempt_action(conn, {"id": 10}, 64, "Banned"))
    assert 10 in lcu_watch.STATE.handled_actions


def test_attempt_action_first_failure_retries(run):
    conn = FakeConnection(_patch_status_handler([500]))
    run(lcu_watch.attempt_action(conn, {"id": 10}, 64, "Banned"))
    assert 10 not in lcu_watch.STATE.handled_actions  # left for a retry
    assert lcu_watch.STATE.action_attempts[10] == 1


def test_attempt_action_gives_up_after_max(run):
    conn = FakeConnection(_patch_status_handler([500] * lcu_watch.MAX_ACTION_ATTEMPTS))
    for _ in range(lcu_watch.MAX_ACTION_ATTEMPTS):
        run(lcu_watch.attempt_action(conn, {"id": 10}, 64, "Banned"))
    assert 10 in lcu_watch.STATE.handled_actions
    assert lcu_watch.STATE.action_attempts[10] == lcu_watch.MAX_ACTION_ATTEMPTS


# --------------------------------------------------------------------------- #
# setup_queue orchestration  [TO_FIX #5]
# --------------------------------------------------------------------------- #
def test_setup_queue_no_start_sets_roles_only(run):
    ap = lcu_watch.Autopilot(
        mode="flex", lanes={"JUNGLE": [35, 233]}, lane_order=["JUNGLE"], bans=[], start=False
    )

    def handler(method, endpoint, body):
        if endpoint == "/lol-lobby/v2/lobby" and method == "get":
            return FakeResponse(200, {"members": [{}]})
        return None

    conn = FakeConnection(handler)
    run(lcu_watch.setup_queue(conn, ap))
    assert conn.posts() == []  # no lobby creation, no search
    assert conn.puts()  # but roles were set


def test_setup_queue_creates_lobby_and_searches(run):
    ap = lcu_watch.Autopilot(mode="solo", lanes={}, lane_order=[], bans=[], start=True)

    def handler(method, endpoint, body):
        if "game-queues" in endpoint:
            return FakeResponse(200, [{"id": lcu_watch.QUEUE_SOLO, "queueAvailability": "Available"}])
        if endpoint == "/lol-lobby/v2/lobby" and method == "post":
            return FakeResponse(200)
        return None

    conn = FakeConnection(handler)
    run(lcu_watch.setup_queue(conn, ap))
    assert "/lol-lobby/v2/lobby" in conn.posts()
    assert "/lol-lobby/v2/lobby/matchmaking/search" in conn.posts()


def test_setup_queue_raises_when_all_candidates_rejected(run):
    ap = lcu_watch.Autopilot(mode="solo", lanes={}, lane_order=[], bans=[], start=True)
    conn = FakeConnection(lambda m, e, b: FakeResponse(400) if m == "post" else None)
    with pytest.raises(lcu_watch.QueueNotAvailableError):
        run(lcu_watch.setup_queue(conn, ap))
    assert "/lol-lobby/v2/lobby/matchmaking/search" not in conn.posts()


# --------------------------------------------------------------------------- #
# CLI validation -> SystemExit  [TO_FIX #6]
# --------------------------------------------------------------------------- #
def test_build_autopilot_unknown_lane_exits():
    args = lcu_watch.parse_args(["--mode", "flex", "--lane", "banana", "Shaco", "Briar"])
    with pytest.raises(SystemExit):
        lcu_watch.build_autopilot(args)


def test_build_autopilot_duplicate_lane_exits(monkeypatch):
    monkeypatch.setattr(lcu_watch.opgg_runes, "resolve_champion", _fake_resolve)
    args = lcu_watch.parse_args(
        ["--mode", "flex", "--lane", "jungle", "Shaco", "Briar",
         "--lane", "jungle", "Lee Sin", "Zed"]
    )
    with pytest.raises(SystemExit):
        lcu_watch.build_autopilot(args)


def test_main_rejects_more_than_two_lanes(monkeypatch):
    monkeypatch.setattr(lcu_watch.opgg_runes, "resolve_champion", _fake_resolve)
    monkeypatch.setattr(lcu_watch.connector, "start", lambda: None)  # never reached
    argv = ["--mode", "flex",
            "--lane", "top", "Shaco", "Briar",
            "--lane", "mid", "Ahri", "Lux",
            "--lane", "jungle", "Lee Sin", "Zed"]
    with pytest.raises(SystemExit):
        lcu_watch.main(argv)


# --------------------------------------------------------------------------- #
# Quick pure-helper wins  [TO_FIX #7]
# --------------------------------------------------------------------------- #
def test_champ_name_sentinels_and_fallback():
    assert lcu_watch.champ_name(0) is None
    assert lcu_watch.champ_name(-1) is None
    assert lcu_watch.champ_name(None) is None
    lcu_watch._champ_names[64] = "Lee Sin"
    assert lcu_watch.champ_name(64) == "Lee Sin"
    assert lcu_watch.champ_name(999) == "Champion#999"


def test_spell_name_sentinels_and_fallback():
    assert lcu_watch.spell_name(0) == "-"
    assert lcu_watch.spell_name(None) == "-"
    lcu_watch._spell_names[4] = "Flash"
    assert lcu_watch.spell_name(4) == "Flash"
    assert lcu_watch.spell_name(99) == "Spell#99"


def test_summarize_dedupes_and_detects_change():
    def session(intent):
        return {
            "localPlayerCellId": 2,
            "myTeam": [{"cellId": 2, "championId": 0, "championPickIntent": intent}],
            "theirTeam": [],
            "actions": [[{"actorCellId": 2, "type": "pick", "championId": 0, "completed": False}]],
        }

    assert lcu_watch.summarize(session(64)) == lcu_watch.summarize(session(64))
    assert lcu_watch.summarize(session(64)) != lcu_watch.summarize(session(99))


# --------------------------------------------------------------------------- #
# set_role_prefs remaining branches  [TO_FIX #8]
# --------------------------------------------------------------------------- #
def _members_handler(n):
    def handler(method, endpoint, body):
        if endpoint == "/lol-lobby/v2/lobby/members" and method == "get":
            return FakeResponse(200, [{}] * n)
        return None

    return handler


def test_set_role_prefs_single_lane_fills_second(run):
    ap = lcu_watch.Autopilot(mode="flex", lanes={"JUNGLE": [35, 233]}, lane_order=["JUNGLE"], bans=[])
    conn = FakeConnection(_members_handler(3))
    run(lcu_watch.set_role_prefs(conn, ap))
    assert conn.puts() == [{"firstPreference": "JUNGLE", "secondPreference": "FILL"}]


def test_set_role_prefs_aram_is_noop(run):
    ap = lcu_watch.Autopilot(mode="aram", lanes={}, lane_order=[], bans=[])
    conn = FakeConnection()
    run(lcu_watch.set_role_prefs(conn, ap))
    assert conn.calls == []  # early return, no requests at all


def test_set_role_prefs_warns_on_failure(run, capsys):
    ap = lcu_watch.Autopilot(
        mode="flex", lanes={"JUNGLE": [35, 233], "MIDDLE": [103, 99]},
        lane_order=["JUNGLE", "MIDDLE"], bans=[],
    )

    def handler(method, endpoint, body):
        if endpoint == "/lol-lobby/v2/lobby/members" and method == "get":
            return FakeResponse(200, [{}] * 2)
        if "position-preferences" in endpoint and method == "put":
            return FakeResponse(400)
        return None

    run(lcu_watch.set_role_prefs(FakeConnection(handler), ap))
    assert "could not set roles" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------- #
# member_names: batch puuid -> name resolution  [TO_FIX #9]
# --------------------------------------------------------------------------- #
def _names_handler(by_puuid):
    """POST /lol-summoner/v2/summoners/puuid -> a summoner DTO per known puuid."""

    def handler(method, endpoint, body):
        if endpoint == "/lol-summoner/v2/summoners/puuid" and method == "post":
            return FakeResponse(200, [
                {"puuid": p, "gameName": by_puuid[p]} for p in body if p in by_puuid
            ])
        return None

    return handler


def test_member_names_resolves_in_one_request(run):
    conn = FakeConnection(_names_handler({"a": "Reze", "b": "Snt29"}))
    assert run(lcu_watch.member_names(conn, ["a", "b"])) == {"a": "Reze", "b": "Snt29"}
    posts = [(e, b) for (m, e, b) in conn.calls if m == "post"]
    assert posts == [("/lol-summoner/v2/summoners/puuid", ["a", "b"])]  # one batch call


def test_member_names_no_request_when_empty(run):
    conn = FakeConnection()
    assert run(lcu_watch.member_names(conn, [None, ""])) == {}
    assert conn.calls == []  # nothing to resolve -> no request


def test_member_names_degrades_on_error(run):
    conn = FakeConnection(lambda m, e, b: FakeResponse(500))
    assert run(lcu_watch.member_names(conn, ["a"])) == {}


def test_member_names_drops_nameless_dtos(run):
    # A DTO with no gameName must be omitted, never mapped to None (dict[str, str]).
    def handler(method, endpoint, body):
        if endpoint == "/lol-summoner/v2/summoners/puuid" and method == "post":
            return FakeResponse(200, [
                {"puuid": "a", "gameName": "Reze"},
                {"puuid": "b", "gameName": ""},   # unresolved -> dropped
                {"puuid": "c"},                   # missing entirely -> dropped
            ])
        return None

    assert run(lcu_watch.member_names(FakeConnection(handler), ["a", "b", "c"])) == {"a": "Reze"}


def test_print_lobby_marks_you_and_batches_names(run, capsys):
    def handler(method, endpoint, body):
        if endpoint == "/lol-lobby/v2/lobby" and method == "get":
            return FakeResponse(200, {
                "gameConfig": {"queueId": 440, "gameMode": "CLASSIC"},
                "localMember": {"puuid": "a"},
                "members": [{"puuid": "a"}, {"puuid": "b"}],
            })
        return _names_handler({"a": "Reze", "b": "Snt29"})(method, endpoint, body)

    conn = FakeConnection(handler)
    assert run(lcu_watch.print_lobby(conn)) is True
    out = capsys.readouterr().out
    assert "Reze <-- You" in out and "Snt29" in out
    # the summoner endpoint is hit once for the whole party, not once per member
    lookups = [e for (m, e, b) in conn.calls if e == "/lol-summoner/v2/summoners/puuid"]
    assert len(lookups) == 1


# --------------------------------------------------------------------------- #
# current_game_mode / get_lobby_members parsing  [TO_FIX #10]
# --------------------------------------------------------------------------- #
def _session_handler(data, status=200):
    return lambda m, e, b: (
        FakeResponse(status, data) if e == "/lol-gameflow/v1/session" else None
    )


def test_current_game_mode_prefers_queue(run):
    data = {"gameData": {"queue": {"gameMode": "ARAM"}}, "map": {"gameMode": "CLASSIC"}}
    assert run(lcu_watch.current_game_mode(FakeConnection(_session_handler(data)))) == "ARAM"


def test_current_game_mode_falls_back_to_map(run):
    data = {"gameData": {}, "map": {"gameMode": "CHERRY"}}
    assert run(lcu_watch.current_game_mode(FakeConnection(_session_handler(data)))) == "CHERRY"


def test_current_game_mode_non_200(run):
    conn = FakeConnection(_session_handler({}, status=404))
    assert run(lcu_watch.current_game_mode(conn)) == ""


def test_get_lobby_members_returns_list(run):
    conn = FakeConnection(_members_handler(3))
    assert len(run(lcu_watch.get_lobby_members(conn))) == 3


def test_get_lobby_members_empty_lobby_is_empty(run):
    conn = FakeConnection(_members_handler(0))
    assert run(lcu_watch.get_lobby_members(conn)) == []


def test_get_lobby_members_non_200_is_empty(run):
    # Honest "no members reported" rather than a fabricated count of 1.
    conn = FakeConnection(lambda m, e, b: FakeResponse(404))
    assert run(lcu_watch.get_lobby_members(conn)) == []


# --------------------------------------------------------------------------- #
# set_spells  [TO_FIX #12]
# --------------------------------------------------------------------------- #
def test_set_spells_noop_when_too_few(run):
    conn = FakeConnection()
    run(lcu_watch.set_spells(conn, [4]))
    assert conn.calls == []


def test_set_spells_patches_selection(run):
    conn = FakeConnection()
    run(lcu_watch.set_spells(conn, [4, 11]))
    assert conn.patches() == [
        ("/lol-champ-select/v1/session/my-selection", {"spell1Id": 4, "spell2Id": 11})
    ]


def test_set_spells_warns_on_failure(run, capsys):
    conn = FakeConnection(lambda m, e, b: FakeResponse(400))
    run(lcu_watch.set_spells(conn, [4, 11]))
    assert "summoner spells" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------- #
# ChampSelectState.reset / on_champ_select_end  [TO_FIX #14]
# --------------------------------------------------------------------------- #
def test_champ_select_state_reset():
    st = lcu_watch.ChampSelectState()
    st.last_snapshot = (1,)
    st.applied_for = (2, "mid")
    st.handled_actions.add(10)
    st.action_attempts[10] = 3
    st.reset()
    assert st.last_snapshot is None
    assert st.applied_for is None
    assert st.handled_actions == set()
    assert st.action_attempts == {}


def test_on_champ_select_end_clears_state(run):
    lcu_watch.STATE.handled_actions.add(5)
    lcu_watch.STATE.action_attempts[5] = 2
    lcu_watch.STATE.last_snapshot = (1,)
    run(lcu_watch.on_champ_select_end(FakeConnection(), object()))
    assert lcu_watch.STATE.handled_actions == set()
    assert lcu_watch.STATE.action_attempts == {}
    assert lcu_watch.STATE.last_snapshot is None


# --------------------------------------------------------------------------- #
# apply_build orchestration  [TO_FIX #15]
# --------------------------------------------------------------------------- #
def _ranked_page():
    return opgg_runes.RunePage(
        primary_style=8000, sub_style=8300,
        primary_rune_ids=[1], secondary_rune_ids=[2], stat_mod_ids=[3],
    )


def test_apply_build_skips_unsupported_mode(run, monkeypatch):
    async def fake_mode(conn):
        return "CHERRY"  # Arena -> no OP.GG runes build

    monkeypatch.setattr(lcu_watch, "current_game_mode", fake_mode)
    calls = []

    async def fake_runes(conn, body):
        calls.append("runes")

    async def fake_spells(conn, spells):
        calls.append("spells")

    monkeypatch.setattr(lcu_watch, "set_runes", fake_runes)
    monkeypatch.setattr(lcu_watch, "set_spells", fake_spells)
    run(lcu_watch.apply_build(FakeConnection(), 64, "jungle"))
    assert calls == []  # left the client alone


def test_apply_build_ranked_passes_mapped_lane(run, monkeypatch):
    async def fake_mode(conn):
        return "CLASSIC"

    monkeypatch.setattr(lcu_watch, "current_game_mode", fake_mode)
    seen = {}

    def fake_best_build(cid, region, mode, preferred):
        seen.update(cid=cid, mode=mode, preferred=preferred)
        return opgg_runes.Build(position="mid", runes=[_ranked_page()], spells=[[4, 11]])

    monkeypatch.setattr(lcu_watch.opgg_runes, "best_build", fake_best_build)

    async def noop(*args):
        pass

    monkeypatch.setattr(lcu_watch, "set_runes", noop)
    monkeypatch.setattr(lcu_watch, "set_spells", noop)
    run(lcu_watch.apply_build(FakeConnection(), 103, "middle"))
    assert seen["preferred"] == "mid"  # LCU 'middle' -> OP.GG 'mid'
    assert seen["mode"] == "ranked"


def test_apply_build_warns_on_fetch_error(run, monkeypatch, capsys):
    async def fake_mode(conn):
        return "CLASSIC"

    monkeypatch.setattr(lcu_watch, "current_game_mode", fake_mode)

    def boom(*args):
        raise RuntimeError("no build")

    monkeypatch.setattr(lcu_watch.opgg_runes, "best_build", boom)
    run(lcu_watch.apply_build(FakeConnection(), 103, "middle"))
    assert "could not fetch build" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------- #
# Shim forwarding contract + runtime AUTO_APPLY toggle  [REVIEW H2 / M3]
# --------------------------------------------------------------------------- #
def test_shim_resolves_reads_and_writes_to_one_owner(monkeypatch):
    # Writing `lcu_watch.X` must land on the module that owns X, and reading it
    # back must resolve to that same module -- no shim-local divergence.
    from src import constants, state

    monkeypatch.setattr(lcu_watch, "AUTO_APPLY", False)
    assert constants.AUTO_APPLY is False        # write reached the owner
    assert lcu_watch.AUTO_APPLY is False         # read resolves to the same owner

    sentinel = object()
    monkeypatch.setattr(lcu_watch, "AUTOPILOT", sentinel)
    assert state.AUTOPILOT is sentinel
    assert lcu_watch.AUTOPILOT is sentinel


class _Event:
    """Minimal stand-in for an lcu-driver websocket event (just `.data`)."""

    def __init__(self, data):
        self.data = data


def _locked_session(champ_id=42, cell=1, position="middle"):
    return {
        "localPlayerCellId": cell,
        "actions": [[{"actorCellId": cell, "type": "pick",
                      "championId": champ_id, "completed": True}]],
        "myTeam": [{"cellId": cell, "assignedPosition": position}],
        "theirTeam": [],
    }


def test_on_champ_select_applies_build_when_auto_apply_on(run, monkeypatch):
    from src import handlers

    applied = []

    async def fake_apply(conn, champ_id, position):
        applied.append((champ_id, position))

    monkeypatch.setattr(handlers, "apply_build", fake_apply)  # the binding the handler calls
    monkeypatch.setattr(lcu_watch, "AUTO_APPLY", True)
    run(lcu_watch.on_champ_select(FakeConnection(), _Event(_locked_session(42, position="middle"))))
    assert applied == [(42, "middle")]


def test_on_champ_select_skips_build_when_auto_apply_off(run, monkeypatch):
    from src import handlers

    applied = []

    async def fake_apply(conn, champ_id, position):
        applied.append((champ_id, position))

    monkeypatch.setattr(handlers, "apply_build", fake_apply)
    monkeypatch.setattr(lcu_watch, "AUTO_APPLY", False)  # toggled live through the shim
    run(lcu_watch.on_champ_select(FakeConnection(), _Event(_locked_session())))
    assert applied == []
