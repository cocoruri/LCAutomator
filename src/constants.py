from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


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


# --- auto-apply config ------------------------------------------------------ #
AUTO_ACCEPT = True  # auto-accept the ready check when a match is found
AUTO_APPLY = True  # set False to only watch, never touch the client
REGION = "global"  # OP.GG region for build lookups
PAGE_PREFIX = "AUTO - "  # rune pages we create are named with this prefix

# OP.GG modes we can actually pull a runes/spells build for. The client's
# gameMode string is mapped onto one of these (see opgg_mode_for); anything
# else (Arena, URF, etc.) is left untouched.
SUPPORTED_MODES = {"ranked", "aram"}


# --- game-protocol strings the client sends --------------------------------- #
# champ-select action types
ACTION_PICK = "pick"
ACTION_BAN = "ban"

# gameMode values
GAME_MODE_ARAM = "ARAM"
GAME_MODE_CLASSIC = "CLASSIC"  # Summoner's Rift (ranked solo/flex, normals)

# gameflow phases
PHASE_LOBBY = "Lobby"

# champ-select timer phases where bans/picks can't yet be completed
PHASE_GAME_STARTING = "GAME_STARTING"
PHASE_PLANNING = "PLANNING"

# ready-check state / our response
READY_CHECK_IN_PROGRESS = "InProgress"
READY_CHECK_NO_RESPONSE = "None"  # we haven't accepted/declined yet

QUEUE_AVAILABLE = "Available"  # queueAvailability value the client uses for live queues

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


# Riot queue ids (gameMode in parens). 2400 ARAM Mayhem reports the rotating
# codename "KIWI"; 1750 Arena reports "CHERRY".
QUEUE_SOLO = 420  # CLASSIC
QUEUE_FLEX = 440  # CLASSIC
QUEUE_ARAM_MAYHEM = 2400  # KIWI
QUEUE_ARAM = 450  # ARAM
QUEUE_ARENA = 1750  # CHERRY -- display only, not startable via --mode

# Single source of truth, keyed by queue id.
# Order matters for --mode aram: try the Mayhem event first, then plain ARAM.
QUEUES = {
    QUEUE_SOLO: Queue("SoloQ", "solo"),
    QUEUE_FLEX: Queue("Flex", "flex"),
    QUEUE_ARAM_MAYHEM: Queue("ARAM Mayhem", "aram"),
    QUEUE_ARAM: Queue("ARAM", "aram"),
    QUEUE_ARENA: Queue("Arena"),
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

# Lane display/sort order, derived from LCU_TO_OPGG so the two can't drift.
POSITION_ORDER = {pos: i for i, pos in enumerate(LCU_TO_OPGG)}
UNRANKED_SORT_KEY = len(POSITION_ORDER)  # sorts unknown/unassigned positions last

FULL_TEAM_SIZE = 5  # a full premade fills every role, so no second preference

# Role-preference sentinels the lobby endpoint accepts for the second slot.
ROLE_UNSELECTED = "UNSELECTED"  # full premade: one fixed role, no backup
ROLE_FILL = "FILL"  # smaller party with a single chosen lane: fill the rest

MAX_ACTION_ATTEMPTS = 8  # give up auto-ban/pick after this many rejected tries
