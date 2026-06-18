from __future__ import annotations

from src.constants import ACTION_BAN, ACTION_PICK


def local_pick(session: dict) -> tuple[int, bool]:
    """(championId, locked) for the local player from the pick actions."""
    cell = session.get("localPlayerCellId")
    champion_id, locked = 0, False
    for group in session.get("actions", []):
        for action in group:
            if action.get("actorCellId") == cell and action.get("type") == ACTION_PICK:
                champion_id = action.get("championId") or champion_id
                locked = action.get("completed", False)
    return champion_id, locked


def unavailable_champions(session: dict) -> tuple[set[int], set[int]]:
    """(banned, taken) champion ids — what we may no longer pick."""
    banned: set[int] = set()
    taken: set[int] = set()
    bans = session.get("bans") or {}
    banned.update(b for b in (bans.get("myTeamBans") or []) if b)
    banned.update(b for b in (bans.get("theirTeamBans") or []) if b)
    for group in session.get("actions", []):
        for action in group:
            cid = action.get("championId") or 0
            if cid and action.get("completed"):
                (banned if action.get("type") == ACTION_BAN else taken).add(cid)
    for team in ("myTeam", "theirTeam"):
        for player in session.get(team, []):
            cid = player.get("championId") or 0
            if cid > 0:
                taken.add(cid)
    return banned, taken


def local_action_in_progress(session: dict, action_type: str) -> dict | None:
    """The local player's active, not-yet-completed ban or pick action."""
    cell = session.get("localPlayerCellId")
    for group in session.get("actions", []):
        for action in group:
            if (
                action.get("actorCellId") == cell
                and action.get("type") == action_type
                and action.get("isInProgress")
                and not action.get("completed")
            ):
                return action
    return None


def local_assigned_position(session: dict) -> str:
    """The local player's raw assignedPosition (e.g. 'middle'), '' if none."""
    cell = session.get("localPlayerCellId")
    for player in session.get("myTeam", []):
        if player.get("cellId") == cell:
            return player.get("assignedPosition") or ""
    return ""


def assigned_lane(session: dict) -> str:
    """The local player's assigned position, canonicalised (e.g. MIDDLE)."""
    return local_assigned_position(session).upper()


def is_aram_session(session: dict) -> bool:
    """True if this champ-select session is ARAM-style (no lanes/bans to draft).

    ARAM has no per-lane pick/ban actions for the autopilot to drive; the client
    signals it with ``benchEnabled`` (the reroll/bench mechanic is ARAM-only).
    Used to gate run_draft off the *session* rather than the configured queue,
    so a watch-only draft game auto-drafts regardless of which queue was joined.
    """
    return bool(session.get("benchEnabled"))
