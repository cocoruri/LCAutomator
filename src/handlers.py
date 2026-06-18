from __future__ import annotations

from lcu_driver import Connector

from src import constants, state
from src.autopilot import QueueNotAvailableError, run_draft, setup_queue
from src.build import apply_build
from src.champ_select import local_assigned_position, local_pick
from src.constants import (
    PHASE_LOBBY,
    READY_CHECK_IN_PROGRESS,
    READY_CHECK_NO_RESPONSE,
)
from src.display import (
    build_champ_select_view,
    load_static,
    print_champ_select,
    queue_label,
    summarize,
)
from src.endpoints import Endpoints
from src.events import (
    ChampSelectEndedUpdate,
    ChampSelectUpdate,
    ConnectedUpdate,
    DisconnectedUpdate,
    NoticeUpdate,
    PhaseUpdate,
    SummonerInfo,
    emit,
)
from src.http import ok

connector = Connector()


# --------------------------------------------------------------------------- #
# Event handlers
# --------------------------------------------------------------------------- #
async def maybe_accept_ready_check(connection, data: dict) -> None:
    """Accept the match if the ready check is live and we haven't responded."""
    if (
        constants.AUTO_ACCEPT  # read live so a runtime toggle takes effect
        and data.get("state") == READY_CHECK_IN_PROGRESS
        and data.get("playerResponse") == READY_CHECK_NO_RESPONSE
    ):
        print("Match found - auto-accepting...")
        await connection.request("post", Endpoints.READY_CHECK_ACCEPT)


def _display_name(dto: dict) -> str | None:
    """The player's Riot ID game name from a summoner/member DTO, or None.

    The legacy displayName/summonerName fields are no longer populated (the
    client can't connect un-updated), so gameName is the only source today.
    """
    return dto.get("gameName")


def _identity(member: dict):
    """Stable id for a lobby member (puuid, else summonerId)."""
    return member.get("puuid") or member.get("summonerId")


async def member_names(connection, puuids: list[str]) -> dict[str, str]:
    """Map puuid -> Riot ID game name for many lobby members in one request.

    Lobby member DTOs don't carry the name, so we POST every puuid to the batch
    summoner endpoint and read each gameName back. Names are cosmetic, so a
    non-2xx response degrades to an empty map (callers fall back to '?').
    """
    puuids = [p for p in puuids if p]
    if not puuids:
        return {}
    resp = await connection.request("post", Endpoints.SUMMONERS_BY_PUUIDS, data=puuids)
    if not ok(resp.status):
        return {}
    # Keep only entries that actually resolved to a name, so the contract stays
    # dict[str, str] (a nameless DTO is dropped rather than mapped to None).
    resolved = ((s.get("puuid"), _display_name(s)) for s in await resp.json())
    return {puuid: name for puuid, name in resolved if puuid and name}


async def print_lobby(connection) -> bool:
    """Print 'Lobby (<mode>)' and the party members. False if not in a lobby."""
    resp = await connection.request("get", Endpoints.LOBBY)
    if not ok(resp.status):
        return False
    lobby = await resp.json()
    cfg = lobby.get("gameConfig") or {}
    label = queue_label(cfg.get("queueId"), cfg.get("gameMode"))
    print(f"  Current phase: Lobby ({label})")
    emit(PhaseUpdate(phase=f"Lobby ({label})"))

    members = lobby.get("members") or []
    local_id = _identity(lobby.get("localMember") or {})
    names = await member_names(connection, [m.get("puuid") for m in members])
    print("  Party:")
    for m in members:
        you = " <-- You" if _identity(m) == local_id else ""
        name = names.get(m.get("puuid")) or m.get("gameName") or "?"
        print(f"    * {name}{you}")
    return True


@connector.ready
async def on_ready(connection):
    state.CONNECTION = connection  # let the GUI act on the client after connect
    print("Connected to the League client.")
    await load_static(connection)

    resp = await connection.request("get", Endpoints.CURRENT_SUMMONER)
    summoner = None
    if ok(resp.status):
        me = await resp.json()
        who = _display_name(me) or "?"
        print(f"  Logged in as {who} (level {me.get('summonerLevel')})")
        summoner = SummonerInfo(name=who, level=me.get("summonerLevel"))
    emit(ConnectedUpdate(summoner=summoner))

    resp = await connection.request("get", Endpoints.GAMEFLOW_PHASE)
    phase = await resp.json() if ok(resp.status) else None
    if phase != PHASE_LOBBY or not await print_lobby(connection):
        print(f"  Current phase: {phase}")
        emit(PhaseUpdate(phase=phase))

    # If we connected mid-ready-check, handle it right away.
    resp = await connection.request("get", Endpoints.READY_CHECK)
    if ok(resp.status):
        await maybe_accept_ready_check(connection, await resp.json())

    print("Watching for changes... (Ctrl+C to stop)\n")

    if state.AUTOPILOT is not None:
        try:
            await setup_queue(connection, state.AUTOPILOT)
        except QueueNotAvailableError as exc:
            print(f"  (error) {exc}")
            emit(NoticeUpdate(text=str(exc), level="error"))


@connector.close
async def on_close(_):
    state.CONNECTION = None
    print("Client closed - stopping watcher.")
    emit(DisconnectedUpdate())


@connector.ws.register(Endpoints.GAMEFLOW_PHASE, event_types=("CREATE", "UPDATE"))
async def on_phase(connection, event):
    print(f"[phase] -> {event.data}")
    emit(PhaseUpdate(phase=event.data))


@connector.ws.register(Endpoints.READY_CHECK, event_types=("CREATE", "UPDATE"))
async def on_ready_check(connection, event):
    """Accept the match when the ready-check popup appears."""
    await maybe_accept_ready_check(connection, event.data or {})


@connector.ws.register(Endpoints.CHAMP_SELECT_SESSION, event_types=("CREATE", "UPDATE"))
async def on_champ_select(connection, event):
    session = event.data

    snapshot = summarize(session)
    if snapshot != state.STATE.last_snapshot:  # the session fires constantly; only
        state.STATE.last_snapshot = snapshot  # print when something actually changed
        print_champ_select(session)
        emit(ChampSelectUpdate(view=build_champ_select_view(session)))

    # Auto-ban / auto-pick first (locking a pick is what triggers auto-apply).
    await run_draft(connection, session)

    # Auto-apply once, the moment the local player locks in a champion.
    champ_id, locked = local_pick(session)
    if constants.AUTO_APPLY and locked and champ_id > 0:  # read live (runtime toggle)
        # Raw (un-cased) position; apply_build lowercases it for the OP.GG map.
        position = local_assigned_position(session)
        if state.STATE.applied_for != (champ_id, position):
            state.STATE.applied_for = (champ_id, position)
            await apply_build(connection, champ_id, position)


@connector.ws.register(Endpoints.CHAMP_SELECT_SESSION, event_types=("DELETE",))
async def on_champ_select_end(connection, event):
    state.STATE.reset()
    print("Champ select ended.\n")
    emit(ChampSelectEndedUpdate())
