from __future__ import annotations

import asyncio

from src import constants
from src.constants import (
    D_SLOT_SPELLS,
    F_SLOT_SPELLS,
    GAME_MODE_ARAM,
    GAME_MODE_CLASSIC,
    LCU_TO_OPGG,
    PAGE_PREFIX,
    SUPPORTED_MODES,
)
from src.display import champ_name, opgg_runes, spell_name
from src.endpoints import Endpoints
from src.http import ok


# --------------------------------------------------------------------------- #
# Applying a build to the client (runes + summoner spells)
# --------------------------------------------------------------------------- #
async def set_runes(connection, page_body: dict) -> None:
    """Overwrite the active rune page in place with the given build.

    Rather than create a page (and juggle slot limits / a fallback guess), we
    edit whichever page is currently selected: find the one flagged "current"
    and PUT the new build onto it. It's already active, so there's nothing else
    to select afterwards.
    """
    pages = await (await connection.request("get", Endpoints.PERKS_PAGES)).json()
    current = next((p for p in pages if p.get("current")), None)
    if current is None:
        print("  (warn) no active rune page to edit; leaving your runes unchanged.")
        return
    if not current.get("isEditable", True):
        print("  (warn) the active rune page can't be edited; leaving your runes unchanged.")
        return

    resp = await connection.request(
        "put", Endpoints.perks_page(current["id"]), data=page_body
    )
    if not ok(resp.status):
        print(f"  (warn) setting runes failed: HTTP {resp.status}")


def arrange_spells(spell_ids: list[int]) -> list[int]:
    """Reorder a spell pair so Flash/Ghost land on D and Smite on F.

    spell1Id is the D slot and spell2Id is the F slot, so sorting by a key of
    0 (D-forced) / 1 (no preference) / 2 (F-forced) puts each spell where it
    belongs while leaving unconstrained spells in their original order.
    """

    if sum(1 for s in spell_ids if s in D_SLOT_SPELLS) > 1:
        # Both want the D slot (e.g. Flash + Ghost): only one can have it. We
        # leave the pair in its given order rather than guessing a winner.
        print("  (warn) both spells prefer the D slot; leaving slot order unchanged.")
        return list(spell_ids)

    def key(spell_id: int) -> int:
        if spell_id in D_SLOT_SPELLS:
            return 0
        if spell_id in F_SLOT_SPELLS:
            return 2
        return 1

    return sorted(spell_ids, key=key)


async def set_spells(connection, spell_ids: list[int]) -> None:
    """Set summoner spells via the champ-select selection endpoint."""
    if len(spell_ids) < 2:
        return
    body = {"spell1Id": spell_ids[0], "spell2Id": spell_ids[1]}
    resp = await connection.request(
        "patch", Endpoints.CHAMP_SELECT_MY_SELECTION, data=body
    )
    if not ok(resp.status):
        print(f"  (warn) setting summoner spells failed: HTTP {resp.status}")


def opgg_mode_for(game_mode: str) -> str | None:
    """Map the client's gameMode to an OP.GG mode, or None if unsupported.

    CLASSIC covers Summoner's Rift (ranked solo/flex, normals); ARAM maps to
    OP.GG's ARAM data. ARAM Mayhem reports the codename "KIWI" and has no runes
    build, so it (like Arena/URF) returns None and is left untouched.
    """
    gm = (game_mode or "").upper()
    if gm == GAME_MODE_ARAM:
        return "aram"
    if gm == GAME_MODE_CLASSIC:
        return "ranked"
    return None  # KIWI (ARAM Mayhem), CHERRY (Arena), URF, ... -> no runes build


async def current_game_mode(connection) -> str:
    """The client's current gameMode string (e.g. CLASSIC, ARAM, KIWI, CHERRY)."""
    resp = await connection.request("get", Endpoints.GAMEFLOW_SESSION)
    if not ok(resp.status):
        return ""
    data = await resp.json()
    queue = (data.get("gameData") or {}).get("queue") or {}
    return queue.get("gameMode") or (data.get("map") or {}).get("gameMode") or ""


async def apply_build(connection, champion_id: int, lcu_position: str) -> None:
    """Fetch OP.GG runes + spells for the locked champion and push them in."""
    if opgg_runes is None:
        return
    name = champ_name(champion_id) or f"Champion#{champion_id}"

    game_mode = await current_game_mode(connection)
    mode = opgg_mode_for(game_mode)
    if mode not in SUPPORTED_MODES:
        print(f"-> Locked {name}. No OP.GG build for mode '{game_mode}'; leaving your setup alone.")
        return

    # ARAM/positionless modes don't use a lane; only ranked needs one.
    preferred = LCU_TO_OPGG.get((lcu_position or "").lower()) if mode == "ranked" else None
    if mode == "ranked":
        src = f"lane '{preferred}'" if preferred else "champion's preferred lane"
    else:
        src = mode.upper()
    print(f"-> Locked {name}. Fetching OP.GG {src} build...")

    # opgg_runes uses blocking urllib; run it off the event loop.
    try:
        build = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: opgg_runes.best_build(champion_id, constants.REGION, mode, preferred),
        )
    except Exception as exc:  # OP.GG fetch (network/no-data) -> skip, keep watching
        print(f"  (warn) could not fetch build: {exc}")
        return

    runes = build.best_runes
    page_body = runes.to_lcu_page(f"{PAGE_PREFIX}{name} {build.position}")
    spells = arrange_spells(build.best_spells)

    d, f = (spells + [0, 0])[:2]
    print(
        f"   Applying {build.position} build: "
        f"D={spell_name(d)} F={spell_name(f)} | perks {runes.selected_perk_ids}"
    )
    await set_runes(connection, page_body)
    await set_spells(connection, spells)
    print("   Done.")
