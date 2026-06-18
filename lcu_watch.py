#!/usr/bin/env python3
"""Watch the League client and print what's happening, focused on champ select.

How the connection works (handled for us by `lcu-driver`):
  * The client writes a `lockfile` and launches `LeagueClientUx.exe` with
    `--app-port=` and `--remoting-auth-token=` on its command line.
  * lcu-driver finds that process, builds the Basic-auth header (user "riot",
    password = the token), and talks to the local HTTPS server on 127.0.0.1.
  * It also opens the LCU WebSocket (WAMP). Subscribing to a resource URI makes
    the client push an event every time that resource changes -- that's how we
    "watch" champ select without polling.

Run it, then open the client: log in, enter a lobby, start a game and pick a
champion. Each meaningful change is printed. Ctrl+C to stop.

When a match is found and AUTO_ACCEPT is on (default), it accepts the ready
check for you (POST /lol-matchmaking/v1/ready-check/accept).

When you LOCK IN a champion and AUTO_APPLY is on (default), it fetches the
OP.GG runes and summoner spells via opgg_runes and pushes them into the client:
  * runes  -> POST /lol-perks/v1/pages (a slot is freed first if you're full)
  * spells -> PATCH /lol-champ-select/v1/session/my-selection
It picks the OP.GG mode from the client's game mode: Summoner's Rift (ranked
solo/flex, normals) uses ranked data, ARAM uses ARAM data. ARAM Mayhem and other
modes with no OP.GG runes build are left untouched.
For ranked the lane comes from the client's assigned position, falling back to
the champion's most-played lane when none is assigned (normals/customs/practice
tool). Modes with no OP.GG runes build (Arena, URF, ...) are left untouched.
Set AUTO_APPLY = False below to watch without changing anything.

Autopilot (optional, CLI-driven): pass --mode to also create the lobby, set
role preferences, start matchmaking, auto-accept, and (in ranked) auto-ban and
auto-pick. With no --mode it just watches.

    python lcu_watch.py                       # watch only
    python lcu_watch.py --mode aram           # queue ARAM Mayhem
    python lcu_watch.py --mode flex \
        --lane jungle Shaco Elise --lane middle Ahri Lux --ban Yasuo Zed
    # In a party you don't own: set roles/champs/bans but let the owner queue.
    python lcu_watch.py --mode flex --lane jungle Shaco Elise --no-start

Draft rules (ranked): ban the 1st ban choice, or the 2nd if the 1st is already
banned; when it's your turn, pick the 1st available champion for your assigned
lane, else the 2nd, else leave it to you. If autofilled to an unconfigured
lane, it leaves the pick to you.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from enum import IntEnum

from lcu_driver import Connector


class SummonerSpell(IntEnum):
    """Riot's canonical summoner-spell ids, by name.

    IntEnum members equal their integer value, so `SummonerSpell.FLASH` works
    anywhere a raw id does (set membership, comparisons, sorting). Only the ones
    referenced in the slot rules below (FLASH, GHOST, SMITE) are used today; the
    rest are kept as a reference for the spell ids the client sends.
    """

    CLEANSE = 1
    EXHAUST = 3
    FLASH = 4
    GHOST = 6
    HEAL = 7
    SMITE = 11
    TELEPORT = 12
    CLARITY = 13
    IGNITE = 14
    BARRIER = 21
    MARK = 32  # Snowball (ARAM)


# Reuse the runes script: champion names + OP.GG build (runes & spells) fetch.
try:
    import opgg_runes
except Exception:  # pragma: no cover - the watcher still works without it
    opgg_runes = None

connector = Connector()

# --- auto-apply config ------------------------------------------------------ #
AUTO_ACCEPT = True  # auto-accept the ready check when a match is found
AUTO_APPLY = True  # set False to only watch, never touch the client
REGION = "global"  # OP.GG region for build lookups
PAGE_PREFIX = "AUTO - "  # rune pages we create are named with this prefix

# OP.GG modes we can actually pull a runes/spells build for. The client's
# gameMode string is mapped onto one of these (see opgg_mode_for); anything
# else (Arena, URF, etc.) is left untouched.
SUPPORTED_MODES = {"ranked", "aram"}

# The client labels lanes differently than OP.GG; map one to the other.
LCU_TO_OPGG = {
    "top": "top",
    "jungle": "jungle",
    "middle": "mid",
    "bottom": "adc",
    "utility": "support",
}

# Summoner-spell key placement (spell1Id = D slot, spell2Id = F slot).
D_SLOT_SPELLS = {SummonerSpell.FLASH, SummonerSpell.GHOST}  # forced onto D
F_SLOT_SPELLS = {SummonerSpell.SMITE}  # forced onto F

# --- autopilot (queue + draft) config --------------------------------------- #
@dataclass(frozen=True)
class Queue:
    name: str  # display name for the lobby readout
    mode: str | None = None  # the --mode that selects it (None = display only)


# Single source of truth, keyed by Riot queue id (gameMode in parens):
#   420 SoloQ / 440 Flex (CLASSIC), 2400 ARAM Mayhem (KIWI), 450 ARAM (ARAM),
#   1750 Arena (CHERRY -- display only, not startable via --mode).
# Order matters for --mode aram: try the Mayhem event first, then plain ARAM.
QUEUES = {
    420: Queue("SoloQ", "solo"),
    440: Queue("Flex", "flex"),
    2400: Queue("ARAM Mayhem", "aram"),
    450: Queue("ARAM", "aram"),
    1750: Queue("Arena"),
}


def _queues_by_mode() -> dict[str, list[int]]:
    """--mode value -> queue ids to try, in order, derived from QUEUES."""
    modes: dict[str, list[int]] = {}
    for queue_id, queue in QUEUES.items():
        if queue.mode:
            modes.setdefault(queue.mode, []).append(queue_id)
    return modes


MODE_QUEUES = _queues_by_mode()  # {"solo": [420], "flex": [440], "aram": [2400, 450]}

# Lane shorthands accepted on the CLI -> the client's canonical position name.
LANE_ALIASES = {
    "top": "TOP",
    "jungle": "JUNGLE", "jg": "JUNGLE",
    "mid": "MIDDLE", "middle": "MIDDLE",
    "adc": "BOTTOM", "bot": "BOTTOM", "bottom": "BOTTOM",
    "support": "UTILITY", "supp": "UTILITY", "sup": "UTILITY", "utility": "UTILITY",
}

POSITION_ORDER = {"top": 0, "jungle": 1, "middle": 2, "bottom": 3, "utility": 4}
UNRANKED_SORT_KEY = len(POSITION_ORDER)  # sorts unknown/unassigned positions last

DEFAULT_OWNED_PAGES = 2  # rune-page slots assumed when the client doesn't report


@dataclass
class Autopilot:
    """What to queue for and how to draft, parsed from the CLI."""

    mode: str  # 'solo' | 'flex' | 'aram'
    lanes: dict[str, list[int]]  # canonical position -> [championId, ...]
    lane_order: list[str]  # positions in preference order (1st, 2nd)
    bans: list[int]  # champion ids to ban, in order
    start: bool = True  # False (--no-start): set roles only, don't queue
    lane_names: dict[str, list[str]] = field(default_factory=dict)  # for logs
    ban_names: list[str] = field(default_factory=list)  # for logs

    @property
    def is_aram(self) -> bool:
        return self.mode == "aram"


@dataclass
class ChampSelectState:
    """Mutable per-champ-select bookkeeping, reset when a session ends."""

    last_snapshot: tuple | None = None  # de-dupes the noisy session stream
    applied_for: tuple | None = None  # (championId, position) we already applied
    handled_actions: set[int] = field(default_factory=set)  # action ids completed
    action_attempts: dict[int, int] = field(default_factory=dict)  # id -> retries

    def reset(self) -> None:
        self.__init__()


_champ_names: dict[int, str] = {}  # championId -> display name (cached on connect)
_spell_names: dict[int, str] = {}  # summonerSpellId -> display name (cached on connect)
STATE = ChampSelectState()  # per-session draft/apply bookkeeping
AUTOPILOT: Autopilot | None = None  # set from CLI args in main()

MAX_ACTION_ATTEMPTS = 8  # give up auto-ban/pick after this many rejected tries


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
        resp = await connection.request(
            "get", "/lol-game-data/assets/v1/summoner-spells.json"
        )
        for spell in await resp.json():
            _spell_names[spell["id"]] = spell["name"]
    except Exception as exc:  # asset fetch/parse -> fall back to "Spell#<id>"
        print(f"  (warn) could not load summoner-spell names: {exc}")


# --------------------------------------------------------------------------- #
# Champ-select parsing
# --------------------------------------------------------------------------- #
def local_pick(session: dict) -> tuple[int, bool]:
    """(championId, locked) for the local player from the pick actions."""
    cell = session.get("localPlayerCellId")
    champion_id, locked = 0, False
    for group in session.get("actions", []):
        for action in group:
            if action.get("actorCellId") == cell and action.get("type") == "pick":
                champion_id = action.get("championId") or champion_id
                locked = action.get("completed", False)
    return champion_id, locked


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


# --------------------------------------------------------------------------- #
# Applying a build to the client (runes + summoner spells)
# --------------------------------------------------------------------------- #
def ok(status: int) -> bool:
    """True for any 2xx response (the LCU uses 200/201/204 interchangeably)."""
    return 200 <= status < 300


async def set_runes(connection, page_body: dict) -> None:
    """Create the rune page and make it active, freeing a slot if needed."""
    pages = await (await connection.request("get", "/lol-perks/v1/pages")).json()

    # Remove any page we created on a previous lock-in so they don't pile up.
    for page in pages:
        if str(page.get("name", "")).startswith(PAGE_PREFIX) and page.get(
            "isDeletable", True
        ):
            await connection.request("delete", f"/lol-perks/v1/pages/{page['id']}")

    # If still at the page limit, delete an editable page to make room.
    inv = await (await connection.request("get", "/lol-perks/v1/inventory")).json()
    owned = inv.get("ownedPageCount", DEFAULT_OWNED_PAGES)
    pages = await (await connection.request("get", "/lol-perks/v1/pages")).json()
    deletable = [p for p in pages if p.get("isDeletable", True)]
    while deletable and len(pages) >= owned:
        victim = next((p for p in deletable if p.get("current")), deletable[0])
        await connection.request("delete", f"/lol-perks/v1/pages/{victim['id']}")
        deletable.remove(victim)
        pages.remove(victim)

    resp = await connection.request("post", "/lol-perks/v1/pages", data=page_body)
    if ok(resp.status):
        created = await resp.json()
        # Belt and suspenders: explicitly select it as the current page.
        await connection.request(
            "put", "/lol-perks/v1/currentpage", data=created.get("id")
        )
    else:
        print(f"  (warn) setting runes failed: HTTP {resp.status}")


def arrange_spells(spell_ids: list[int]) -> list[int]:
    """Reorder a spell pair so Flash/Ghost land on D and Smite on F.

    spell1Id is the D slot and spell2Id is the F slot, so sorting by a key of
    0 (D-forced) / 1 (no preference) / 2 (F-forced) puts each spell where it
    belongs while leaving unconstrained spells in their original order.
    """

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
        "patch", "/lol-champ-select/v1/session/my-selection", data=body
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
    if gm == "ARAM":
        return "aram"
    if gm == "CLASSIC":
        return "ranked"
    return None  # KIWI (ARAM Mayhem), CHERRY (Arena), URF, ... -> no runes build


async def current_game_mode(connection) -> str:
    """The client's current gameMode string (e.g. CLASSIC, ARAM, KIWI, CHERRY)."""
    resp = await connection.request("get", "/lol-gameflow/v1/session")
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
            lambda: opgg_runes.best_build(champion_id, REGION, mode, preferred),
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


# --------------------------------------------------------------------------- #
# Autopilot: starting the queue
# --------------------------------------------------------------------------- #
async def queue_candidates(connection, mode: str) -> list[int]:
    """Ordered queue ids to try for this mode (most specific first).

    Solo/Flex map to a single id. For ARAM we also discover the live event
    queue (e.g. Mayhem) from the client, since its gameMode is a rotating
    codename ("KIWI"); a discovered/known id can still be rejected by lobby
    creation (event not queueable right now), so plain ARAM (450) is the final
    fallback in MODE_QUEUES and start_queue() retries down the list.
    """
    if mode != "aram":
        return list(MODE_QUEUES.get(mode, []))

    discovered: list[int] = []
    resp = await connection.request("get", "/lol-game-queues/v1/queues")
    if ok(resp.status):
        for q in await resp.json():
            text = f"{q.get('name', '')} {q.get('description', '')}".lower()
            if "mayhem" in text and q.get("queueAvailability") == "Available":
                discovered.append(q["id"])

    seen: set[int] = set()  # dedupe, preserve order (discovered, then known/fallback)
    return [q for q in discovered + MODE_QUEUES["aram"] if not (q in seen or seen.add(q))]


async def lobby_member_count(connection) -> int:
    """How many players are in the current lobby (1 if there's no lobby/info)."""
    resp = await connection.request("get", "/lol-lobby/v2/lobby")
    if ok(resp.status):
        return len((await resp.json()).get("members") or []) or 1
    return 1


async def set_role_prefs(connection, ap: Autopilot) -> None:
    """Set the local member's role preference(s) from the chosen lanes (ranked).

    A full 5-premade (flex) assigns one unique role per player, so there's only
    a single role to set; smaller parties (solo/duo/trio) take a first + second
    preference like solo queue.
    """
    if ap.is_aram or not ap.lane_order:
        return
    first = ap.lane_order[0]
    if await lobby_member_count(connection) >= 5:
        second = "UNSELECTED"  # 5-stack: one role each
    else:
        second = ap.lane_order[1] if len(ap.lane_order) > 1 else "FILL"

    resp = await connection.request(
        "put",
        "/lol-lobby/v2/lobby/members/localMember/position-preferences",
        data={"firstPreference": first, "secondPreference": second},
    )
    if ok(resp.status):
        shown = first if second == "UNSELECTED" else f"{first} / {second}"
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
            "post", "/lol-lobby/v2/lobby", data={"queueId": queue_id}
        )
        if ok(resp.status):
            created = queue_id
            break
        print(f"  (warn) queueId {queue_id} rejected (HTTP {resp.status}); trying next...")
    if created is None:
        print("  (error) could not create any lobby; giving up.")
        return
    print(f"  Lobby created (queueId={created}).")

    await set_role_prefs(connection, ap)

    resp = await connection.request("post", "/lol-lobby/v2/lobby/matchmaking/search")
    if ok(resp.status):
        print("  Searching for a match...")
    else:
        print(f"  (warn) could not start search: HTTP {resp.status}")


# --------------------------------------------------------------------------- #
# Autopilot: drafting (ranked ban / pick)
# --------------------------------------------------------------------------- #
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
                (banned if action.get("type") == "ban" else taken).add(cid)
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


def assigned_lane(session: dict) -> str:
    """The local player's assigned position, canonicalised (e.g. MIDDLE)."""
    cell = session.get("localPlayerCellId")
    for player in session.get("myTeam", []):
        if player.get("cellId") == cell:
            return (player.get("assignedPosition") or "").upper()
    return ""


async def complete_action(connection, action_id: int, champion_id: int) -> int:
    """PATCH an action to completed; returns the HTTP status."""
    resp = await connection.request(
        "patch",
        f"/lol-champ-select/v1/session/actions/{action_id}",
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
    attempts = STATE.action_attempts.get(aid, 0) + 1
    STATE.action_attempts[aid] = attempts
    status = await complete_action(connection, aid, champion_id)
    if ok(status):
        STATE.handled_actions.add(aid)
        print(f"   {verb} {champ_name(champion_id)}.")
    elif attempts == 1:
        print(f"   (warn) {verb.lower()} {champ_name(champion_id)} failed (HTTP {status}); retrying...")
    elif attempts >= MAX_ACTION_ATTEMPTS:
        STATE.handled_actions.add(aid)
        print(f"   (warn) gave up on {verb.lower()} after {attempts} tries (HTTP {status}).")


async def run_draft(connection, session: dict) -> None:
    """Auto-ban and auto-pick in ranked, per the configured preferences."""
    ap = AUTOPILOT
    if ap is None or ap.is_aram:
        return
    # During the opening phases ("GAME_STARTING" then "PLANNING", where players
    # only declare pick intent) the ban action is already flagged in-progress,
    # but the client rejects completing it. Only act once bans/picks are live.
    phase = (session.get("timer") or {}).get("phase")
    if phase in ("GAME_STARTING", "PLANNING"):
        return
    banned, taken = unavailable_champions(session)

    # --- Ban: first choice, unless already banned -> second choice. ---------- #
    ban = local_action_in_progress(session, "ban")
    if ban and ban["id"] not in STATE.handled_actions:
        target = next((b for b in ap.bans if b not in banned), None)
        if target is None:
            STATE.handled_actions.add(ban["id"])
            print("   Both ban options already banned - skipping ban.")
        else:
            await attempt_action(connection, ban, target, "Banned")
        return

    # --- Pick: first available choice for the assigned lane. ----------------- #
    pick = local_action_in_progress(session, "pick")
    if pick and pick["id"] not in STATE.handled_actions:
        lane = assigned_lane(session)
        choices = ap.lanes.get(lane)
        if not choices:
            STATE.handled_actions.add(pick["id"])
            print(f"   Autofilled to {lane or '?'} (no preset) - pick yourself.")
            return
        target = next((c for c in choices if c not in (banned | taken)), None)
        if target is None:
            STATE.handled_actions.add(pick["id"])
            print(f"   Both {lane} choices unavailable - pick yourself.")
        else:
            await attempt_action(connection, pick, target, "Picked")


# --------------------------------------------------------------------------- #
# Event handlers
# --------------------------------------------------------------------------- #
async def maybe_accept_ready_check(connection, data: dict) -> None:
    """Accept the match if the ready check is live and we haven't responded."""
    if (
        AUTO_ACCEPT
        and data.get("state") == "InProgress"
        and data.get("playerResponse") == "None"
    ):
        print("Match found - auto-accepting...")
        await connection.request("post", "/lol-matchmaking/v1/ready-check/accept")


def queue_label(queue_id, game_mode: str = "") -> str:
    """Friendly lobby label, e.g. 'Flex' (falls back to gameMode / id)."""
    queue = QUEUES.get(queue_id)
    return (queue.name if queue else "") or game_mode or f"queue {queue_id}"


async def resolve_member_name(connection, member: dict) -> str:
    """Player name for a lobby member.

    Riot IDs replaced summoner names, so the participant DTO usually has no
    name -- we resolve it by puuid (then summonerId) via the summoner endpoint.
    """
    direct = member.get("gameName") or member.get("summonerName")
    if direct:
        return direct
    lookups = []
    if member.get("puuid"):
        lookups.append(f"/lol-summoner/v2/summoners/puuid/{member['puuid']}")
    if member.get("summonerId"):
        lookups.append(f"/lol-summoner/v1/summoners/{member['summonerId']}")
    for url in lookups:
        resp = await connection.request("get", url)
        if ok(resp.status):
            data = await resp.json()
            name = data.get("gameName") or data.get("displayName") or data.get("summonerName")
            if name:
                return name
    return "?"


async def print_lobby(connection) -> bool:
    """Print 'Lobby (<mode>)' and the party members. False if not in a lobby."""
    resp = await connection.request("get", "/lol-lobby/v2/lobby")
    if not ok(resp.status):
        return False
    lobby = await resp.json()
    cfg = lobby.get("gameConfig") or {}
    print(f"  Current phase: Lobby ({queue_label(cfg.get('queueId'), cfg.get('gameMode'))})")

    members = lobby.get("members") or []
    local = lobby.get("localMember") or {}
    local_id = local.get("puuid") or local.get("summonerId")
    print("  Party:")
    for m in members:
        you = " <-- You" if (m.get("puuid") or m.get("summonerId")) == local_id else ""
        print(f"    * {await resolve_member_name(connection, m)}{you}")
    return True


@connector.ready
async def on_ready(connection):
    print("✓ Connected to the League client.")
    await load_static(connection)

    resp = await connection.request("get", "/lol-summoner/v1/current-summoner")
    if ok(resp.status):
        me = await resp.json()
        who = me.get("gameName") or me.get("displayName") or "?"
        print(f"  Logged in as {who} (level {me.get('summonerLevel')})")

    resp = await connection.request("get", "/lol-gameflow/v1/gameflow-phase")
    phase = await resp.json() if ok(resp.status) else None
    if phase != "Lobby" or not await print_lobby(connection):
        print(f"  Current phase: {phase}")

    # If we connected mid-ready-check, handle it right away.
    resp = await connection.request("get", "/lol-matchmaking/v1/ready-check")
    if ok(resp.status):
        await maybe_accept_ready_check(connection, await resp.json())

    print("Watching for changes... (Ctrl+C to stop)\n")

    if AUTOPILOT is not None:
        await setup_queue(connection, AUTOPILOT)


@connector.close
async def on_close(_):
    print("Client closed - stopping watcher.")


@connector.ws.register(
    "/lol-gameflow/v1/gameflow-phase", event_types=("CREATE", "UPDATE")
)
async def on_phase(connection, event):
    print(f"[phase] -> {event.data}")


@connector.ws.register(
    "/lol-matchmaking/v1/ready-check", event_types=("CREATE", "UPDATE")
)
async def on_ready_check(connection, event):
    """Accept the match when the ready-check popup appears."""
    await maybe_accept_ready_check(connection, event.data or {})


@connector.ws.register("/lol-champ-select/v1/session", event_types=("CREATE", "UPDATE"))
async def on_champ_select(connection, event):
    session = event.data

    snapshot = summarize(session)
    if snapshot != STATE.last_snapshot:  # the session fires constantly; only
        STATE.last_snapshot = snapshot  # print when something actually changed
        print_champ_select(session)

    # Auto-ban / auto-pick first (locking a pick is what triggers auto-apply).
    await run_draft(connection, session)

    # Auto-apply once, the moment the local player locks in a champion.
    champ_id, locked = local_pick(session)
    if AUTO_APPLY and locked and champ_id > 0:
        cell = session.get("localPlayerCellId")
        position = next(
            (
                p.get("assignedPosition")
                for p in session.get("myTeam", [])
                if p.get("cellId") == cell
            ),
            "",
        )
        if STATE.applied_for != (champ_id, position):
            STATE.applied_for = (champ_id, position)
            await apply_build(connection, champ_id, position)


@connector.ws.register("/lol-champ-select/v1/session", event_types=("DELETE",))
async def on_champ_select_end(connection, event):
    STATE.reset()
    print("Champ select ended.\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _resolve_champion(name: str) -> tuple[int, str]:
    """Champion name -> (id, display name), via opgg_runes' Data Dragon cache."""
    if opgg_runes is None:
        raise SystemExit("opgg_runes is required to resolve champion names.")
    champ = opgg_runes.resolve_champion(name)
    return champ["id"], champ["name"]


def build_autopilot(args) -> Autopilot:
    lanes: dict[str, list[int]] = {}
    lane_order: list[str] = []
    lane_names: dict[str, list[str]] = {}
    for position, champ1, champ2 in args.lane or []:
        canon = LANE_ALIASES.get(position.lower())
        if not canon:
            raise SystemExit(f"Unknown lane {position!r}. Use one of {sorted(set(LANE_ALIASES))}.")
        if canon in lanes:
            raise SystemExit(f"Lane {canon} given twice.")
        ids, names = [], []
        for cname in (champ1, champ2):
            cid, disp = _resolve_champion(cname)
            ids.append(cid)
            names.append(disp)
        lanes[canon] = ids
        lane_names[canon] = names
        lane_order.append(canon)

    bans: list[int] = []
    ban_names: list[str] = []
    for cname in args.ban or []:
        cid, disp = _resolve_champion(cname)
        bans.append(cid)
        ban_names.append(disp)

    return Autopilot(
        mode=args.mode,
        lanes=lanes,
        lane_order=lane_order,
        bans=bans,
        start=not args.no_start,
        lane_names=lane_names,
        ban_names=ban_names,
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Watch the League client; optionally auto-queue, draft, and set runes."
    )
    p.add_argument(
        "--mode",
        choices=["solo", "flex", "aram"],
        help="Queue to start (solo/flex = ranked, aram = ARAM Mayhem). Omit to only watch.",
    )
    p.add_argument(
        "--lane",
        nargs=3,
        action="append",
        metavar=("POSITION", "CHAMP1", "CHAMP2"),
        help="A preferred lane and its two champions. Repeatable (max 2). Ranked only.",
    )
    p.add_argument(
        "--ban",
        nargs=2,
        metavar=("CHAMP1", "CHAMP2"),
        help="Two champions to ban (2nd only used if the 1st is already banned).",
    )
    p.add_argument(
        "--no-start",
        action="store_true",
        help="Don't create a lobby or start the queue (e.g. you're a non-owner "
        "party member); just set roles and auto-draft.",
    )
    return p.parse_args(argv)


def main(argv=None):
    global AUTOPILOT
    args = parse_args(argv)
    if args.no_start and not args.mode:
        print("note: --no-start has no effect without --mode (nothing to configure).")
    if args.mode:
        if args.lane and len(args.lane) > 2:
            raise SystemExit("At most two --lane options are supported.")
        AUTOPILOT = build_autopilot(args)
        summary = f"Autopilot armed: mode={args.mode}"
        if not AUTOPILOT.start:
            summary += " (no-start: roles only)"
        if AUTOPILOT.lane_order:
            lanes = "; ".join(
                f"{pos} -> {', '.join(AUTOPILOT.lane_names[pos])}"
                for pos in AUTOPILOT.lane_order
            )
            summary += f" | lanes: {lanes}"
        if AUTOPILOT.ban_names:
            summary += f" | bans: {', '.join(AUTOPILOT.ban_names)}"
        print(summary)

    # On Windows the selector loop avoids noisy ProactorEventLoop teardown
    # errors from aiohttp when you Ctrl+C out of the watcher.
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print("Waiting for the League client... (start it if it isn't running)")
    try:
        connector.start()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
