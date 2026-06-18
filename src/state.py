from __future__ import annotations

from dataclasses import dataclass, field


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
        # Reassign every field __init__ sets, rather than calling __init__ again
        # (which breaks under subclassing and confuses type checkers).
        self.last_snapshot = None
        self.applied_for = None
        self.handled_actions = set()
        self.action_attempts = {}


# These four are module-level because they are shared across the async LCU event
# handlers (on_ready, on_champ_select, ...), which are registered as free
# functions and have no instance to hang state off. A single live client is
# assumed; concurrent connections would need this state scoped per-connection.
_champ_names: dict[int, str] = {}  # championId -> display name; cached on connect, read by every handler
_spell_names: dict[int, str] = {}  # summonerSpellId -> display name; cached on connect, read by every handler
STATE = ChampSelectState()  # per-session draft/apply bookkeeping, mutated from champ-select handlers
AUTOPILOT: Autopilot | None = None  # CLI config set once in main(), read by the queue/draft handlers
