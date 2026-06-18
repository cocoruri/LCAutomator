from __future__ import annotations

from src import state
from src.champ_select import (
    assigned_lane,
    local_action_in_progress,
    unavailable_champions,
)
from src.constants import (
    ACTION_BAN,
    ACTION_PICK,
    FULL_TEAM_SIZE,
    MAX_ACTION_ATTEMPTS,
    MODE_QUEUES,
    PHASE_GAME_STARTING,
    PHASE_PLANNING,
    ROLE_FILL,
    ROLE_UNSELECTED,
)
from src.display import champ_name
from src.endpoints import Endpoints
from src.http import ok
from src.lobby import fetch_available_queues, get_lobby_members
from src.state import Autopilot


class QueueNotAvailableError(Exception):
    """Raised by setup_queue when no lobby could be created for the requested mode."""


# --------------------------------------------------------------------------- #
# Autopilot: starting the queue
# --------------------------------------------------------------------------- #
async def queue_candidates(connection, mode: str) -> list[int]:
    """Available queue ids for the given CLI mode, in preference order.

    Intersects the statically-known queue ids for this mode (from QUEUES /
    MODE_QUEUES) with the live availability data from the client. Only ids
    that are both known for this mode AND currently available are returned, so
    a queue that is PlatformDisabled is never attempted.
    """
    available_ids = {q["id"] for q in await fetch_available_queues(connection)}
    return [qid for qid in MODE_QUEUES.get(mode, []) if qid in available_ids]


async def set_role_prefs(connection, ap: Autopilot) -> None:
    """Set the local member's role preference(s) from the chosen lanes (ranked).

    A full 5-premade (flex) assigns one unique role per player, so there's only
    a single role to set; smaller parties (solo/duo/trio) take a first + second
    preference like solo queue.
    """
    if ap.is_aram or not ap.lane_order:
        return
    first = ap.lane_order[0]
    if len(await get_lobby_members(connection)) >= FULL_TEAM_SIZE:
        second = ROLE_UNSELECTED  # 5-stack: one role each
    else:
        second = ap.lane_order[1] if len(ap.lane_order) > 1 else ROLE_FILL

    resp = await connection.request(
        "put",
        Endpoints.LOBBY_POSITION_PREFERENCES,
        data={"firstPreference": first, "secondPreference": second},
    )
    if ok(resp.status):
        shown = first if second == ROLE_UNSELECTED else f"{first} / {second}"
        print(f"  Roles: {shown}")
    else:
        print(f"  (warn) could not set roles: HTTP {resp.status}")


async def setup_queue(connection, ap: Autopilot) -> None:
    """Set roles and (unless --no-start) create the lobby and start searching.

    With --no-start we assume you're already in a party you don't own: we only
    set your role preferences in the existing lobby and let the owner start the
    queue. Auto-accept and draft automation still run regardless.
    """
    if not ap.start:
        print("Autopilot: --no-start set; configuring roles only (party owner starts the queue).")
        await set_role_prefs(connection, ap)
        return

    candidates = await queue_candidates(connection, ap.mode)
    print(f"Autopilot: creating {ap.mode} lobby (trying queueIds {candidates})...")
    created = None
    for queue_id in candidates:
        resp = await connection.request(
            "post", Endpoints.LOBBY, data={"queueId": queue_id}
        )
        if ok(resp.status):
            created = queue_id
            break
        print(f"  (warn) queueId {queue_id} rejected (HTTP {resp.status}); trying next...")
    if created is None:
        raise QueueNotAvailableError(
            f"Could not create a {ap.mode!r} lobby (tried queueIds {candidates}). "
            "The queue may not be active right now."
        )
    print(f"  Lobby created (queueId={created}).")

    await set_role_prefs(connection, ap)

    resp = await connection.request("post", Endpoints.LOBBY_SEARCH)
    if ok(resp.status):
        print("  Searching for a match...")
    else:
        print(f"  (warn) could not start search: HTTP {resp.status}")


# --------------------------------------------------------------------------- #
# Autopilot: drafting (ranked ban / pick)
# --------------------------------------------------------------------------- #
async def complete_action(connection, action_id: int, champion_id: int) -> int:
    """PATCH an action to completed; returns the HTTP status."""
    resp = await connection.request(
        "patch",
        Endpoints.champ_select_action(action_id),
        data={"championId": champion_id, "completed": True},
    )
    return resp.status


async def attempt_action(connection, action: dict, champion_id: int, verb: str) -> None:
    """Try to complete a ban/pick, retrying across updates if the client rejects it.

    The client can reject a completion in the first moments of a phase (the ban
    phase is the very first thing in champ select), so we retry on subsequent
    session updates rather than giving up after one silent try.
    """
    aid = action["id"]
    attempts = state.STATE.action_attempts.get(aid, 0) + 1
    state.STATE.action_attempts[aid] = attempts
    status = await complete_action(connection, aid, champion_id)
    if ok(status):
        state.STATE.handled_actions.add(aid)
        print(f"   {verb} {champ_name(champion_id)}.")
    elif attempts == 1:
        print(f"   (warn) {verb.lower()} {champ_name(champion_id)} failed (HTTP {status}); retrying...")
    elif attempts >= MAX_ACTION_ATTEMPTS:
        state.STATE.handled_actions.add(aid)
        print(f"   (warn) gave up on {verb.lower()} after {attempts} tries (HTTP {status}).")


async def run_draft(connection, session: dict) -> None:
    """Auto-ban and auto-pick in ranked, per the configured preferences."""
    ap = state.AUTOPILOT
    if ap is None or ap.is_aram:
        return
    # During the opening phases ("GAME_STARTING" then "PLANNING", where players
    # only declare pick intent) the ban action is already flagged in-progress,
    # but the client rejects completing it. Only act once bans/picks are live.
    phase = (session.get("timer") or {}).get("phase")
    if phase in (PHASE_GAME_STARTING, PHASE_PLANNING):
        return
    banned, taken = unavailable_champions(session)

    # --- Ban: first choice, unless already banned -> second choice. ---------- #
    ban = local_action_in_progress(session, ACTION_BAN)
    if ban and ban["id"] not in state.STATE.handled_actions:
        target = next((b for b in ap.bans if b not in banned), None)
        if target is None:
            state.STATE.handled_actions.add(ban["id"])
            print("   Both ban options already banned - skipping ban.")
        else:
            await attempt_action(connection, ban, target, "Banned")
        return

    # --- Pick: first available choice for the assigned lane. ----------------- #
    pick = local_action_in_progress(session, ACTION_PICK)
    if pick and pick["id"] not in state.STATE.handled_actions:
        lane = assigned_lane(session)
        choices = ap.lanes.get(lane)
        if not choices:
            state.STATE.handled_actions.add(pick["id"])
            print(f"   Autofilled to {lane or '?'} (no preset) - pick yourself.")
            return
        target = next((c for c in choices if c not in (banned | taken)), None)
        if target is None:
            state.STATE.handled_actions.add(pick["id"])
            print(f"   Both {lane} choices unavailable - pick yourself.")
        else:
            await attempt_action(connection, pick, target, "Picked")
