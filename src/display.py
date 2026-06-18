from __future__ import annotations

from src.champ_select import local_pick
from src.constants import (
    LCU_TO_OPGG,
    POSITION_ORDER,
    QUEUES,
    UNRANKED_SORT_KEY,
)
from src.endpoints import Endpoints
from src.events import ChampSelectView, PlayerView
from src.state import _champ_names, _spell_names


# Reuse the runes script: champion names + OP.GG build (runes & spells) fetch.
try:
    import opgg_runes
except ImportError as exc:  # pragma: no cover - the watcher still works without it
    print(f"(warn) opgg_runes unavailable; runes/spells auto-apply disabled: {exc!r}")
    opgg_runes = None


# --------------------------------------------------------------------------- #
# Lookups
#
# Placeholder convention: an empty selection (id <= 0) returns a *sentinel* the
# caller can test -- None for champions (so `if name:` filters empties), "-" for
# spells (always rendered in a fixed-width line). A real id with no known name
# falls back to a "<Thing>#<id>" string so the id is still visible.
# --------------------------------------------------------------------------- #
def champ_name(champion_id: int | None) -> str | None:
    """Champion display name; None when nothing is selected (id <= 0)."""
    if not champion_id or champion_id <= 0:
        return None
    return _champ_names.get(champion_id, f"Champion#{champion_id}")


def spell_name(spell_id: int | None) -> str:
    """Spell display name; "-" when nothing is selected (id <= 0)."""
    if not spell_id or spell_id <= 0:
        return "-"
    return _spell_names.get(spell_id, f"Spell#{spell_id}")


async def load_static(connection) -> None:
    """Fill the id->name maps once, on connect.

    Names are cosmetic, so a network/parse failure here must never stop the
    watcher -- both lookups catch broadly and degrade to id-based placeholders.
    """
    if opgg_runes is not None:
        try:
            for entry in opgg_runes.champion_index().values():
                _champ_names[entry["id"]] = entry["name"]
        except Exception as exc:  # network/parse -> fall back to "Champion#<id>"
            print(f"  (warn) could not load champion names: {exc}")
    # Summoner-spell names come straight from the client's bundled assets,
    # so this part needs no internet connection.
    try:
        resp = await connection.request("get", Endpoints.SUMMONER_SPELLS_ASSETS)
        for spell in await resp.json():
            _spell_names[spell["id"]] = spell["name"]
    except Exception as exc:  # asset fetch/parse -> fall back to "Spell#<id>"
        print(f"  (warn) could not load summoner-spell names: {exc}")


def player_line(player: dict, is_me: bool) -> str:
    pos = (player.get("assignedPosition") or "").lower()
    pos_label = LCU_TO_OPGG.get(pos, pos or "?")  # middle->mid, bottom->adc, etc.
    locked = champ_name(player.get("championId"))
    intent = champ_name(player.get("championPickIntent"))
    if locked:
        pick = f"{locked} (locked)"
    elif intent:
        pick = f"{intent} (hovering)"
    else:
        pick = "-"
    me = " <- you" if is_me else ""
    spells = ""
    if is_me:
        spells = f"  spells: {spell_name(player.get('spell1Id'))} + {spell_name(player.get('spell2Id'))}"
    return f"  {pos_label:<8} {pick}{spells}{me}"


def summarize(session: dict) -> tuple:
    """A hashable view of the bits we care about, to skip redundant prints."""
    my = tuple(
        (p.get("cellId"), p.get("championId"), p.get("championPickIntent"))
        for p in session.get("myTeam", [])
    )
    their = tuple(p.get("championId") for p in session.get("theirTeam", []))
    return (local_pick(session), my, their)


def print_champ_select(session: dict) -> None:
    champ_id, locked = local_pick(session)
    name = champ_name(champ_id)
    if name:
        state = "LOCKED IN" if locked else "hovering"
        print(f"\n=== Champ Select ===  your pick: {name} ({state})")
    else:
        print("\n=== Champ Select ===  (no champion selected yet)")

    cell = session.get("localPlayerCellId")
    my_team = sorted(
        session.get("myTeam", []),
        key=lambda p: POSITION_ORDER.get(
            (p.get("assignedPosition") or "").lower(), UNRANKED_SORT_KEY
        ),
    )
    print("Your team:")
    for player in my_team:
        print(player_line(player, is_me=player.get("cellId") == cell))

    enemy = [champ_name(p.get("championId")) for p in session.get("theirTeam", [])]
    enemy = [c for c in enemy if c]
    if enemy:
        print("Enemy picks (revealed): " + ", ".join(enemy))


def _player_view(player: dict, is_me: bool) -> PlayerView:
    """A render-ready PlayerView for one champ-select cell.

    A locked championId beats a hover (championPickIntent), mirroring player_line.
    """
    pos = (player.get("assignedPosition") or "").lower()
    pos_label = LCU_TO_OPGG.get(pos, pos or "?")
    locked_id = player.get("championId")
    name = champ_name(locked_id) or champ_name(player.get("championPickIntent"))
    return PlayerView(
        position=pos_label,
        champion=name,
        locked=bool(champ_name(locked_id)),
        is_me=is_me,
    )


def _ban_names(ban_ids) -> tuple[str, ...]:
    """Resolve a list of banned champion ids to names, dropping empties."""
    return tuple(n for n in (champ_name(b) for b in (ban_ids or [])) if n)


def build_champ_select_view(session: dict) -> ChampSelectView:
    """A name-resolved, render-ready snapshot of a champ-select session.

    The single transform both front ends use: it reuses the pure champ_select
    readers + the name lookups above so neither the CLI nor the GUI reparses a
    raw session. print_champ_select renders the console form; the GUI renders
    widgets from the same view.
    """
    champ_id, locked = local_pick(session)
    cell = session.get("localPlayerCellId")
    my_team = sorted(
        session.get("myTeam", []),
        key=lambda p: POSITION_ORDER.get(
            (p.get("assignedPosition") or "").lower(), UNRANKED_SORT_KEY
        ),
    )
    bans = session.get("bans") or {}
    return ChampSelectView(
        your_pick=champ_name(champ_id),
        your_pick_locked=locked,
        my_team=tuple(
            _player_view(p, is_me=p.get("cellId") == cell) for p in my_team
        ),
        enemy_champions=tuple(
            n
            for n in (champ_name(p.get("championId")) for p in session.get("theirTeam", []))
            if n
        ),
        my_bans=_ban_names(bans.get("myTeamBans")),
        their_bans=_ban_names(bans.get("theirTeamBans")),
    )


def queue_label(queue_id, game_mode: str = "") -> str:
    """Friendly lobby label, e.g. 'Flex' (falls back to gameMode / id)."""
    queue = QUEUES.get(queue_id)
    return (queue.name if queue else "") or game_mode or f"queue {queue_id}"
