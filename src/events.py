from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Framework-agnostic event sink
#
# handlers.py describes *what happened* by emitting structured Update objects to
# the active sink; it never decides *how* that surfaces. The CLI keeps printing
# to the console exactly as before (its existing print() calls are untouched, so
# the suite stays green); a GUI registers a sink that turns the same Updates into
# Qt signals. No Qt/CLI imports live here, so src/ stays headless.
#
# A single module-level sink mirrors state.py's single-live-client assumption:
# the async handlers are free functions with no instance to hang a sink off.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SummonerInfo:
    """The logged-in summoner, for the GUI's user box."""

    name: str
    level: int | None = None


@dataclass(frozen=True)
class PlayerView:
    """One champ-select cell, already name-resolved for rendering."""

    position: str  # OP.GG-style label (mid/adc/...) or "?"
    champion: str | None  # display name, None if nothing chosen
    locked: bool  # True = locked in, False = hovering/none
    is_me: bool


@dataclass(frozen=True)
class ChampSelectView:
    """A render-ready snapshot of a champ-select session.

    Built once, in display.build_champ_select_view, from the pure champ_select
    readers + display name lookups, so the CLI and GUI never reparse a session.
    """

    your_pick: str | None
    your_pick_locked: bool
    my_team: tuple[PlayerView, ...] = ()
    enemy_champions: tuple[str, ...] = ()  # revealed enemy picks
    my_bans: tuple[str, ...] = ()
    their_bans: tuple[str, ...] = ()


# --- Update payloads ------------------------------------------------------- #
# One small dataclass per kind of thing the handlers report. Frozen so a sink
# can stash/compare them safely; framework-agnostic so any front end consumes them.
@dataclass(frozen=True)
class ConnectedUpdate:
    summoner: SummonerInfo | None


@dataclass(frozen=True)
class DisconnectedUpdate:
    pass


@dataclass(frozen=True)
class PhaseUpdate:
    phase: str | None  # the gameflow-phase enum value, or a "Lobby (...)" label


@dataclass(frozen=True)
class ChampSelectUpdate:
    view: ChampSelectView


@dataclass(frozen=True)
class ChampSelectEndedUpdate:
    pass


@dataclass(frozen=True)
class NoticeUpdate:
    """A free-text status line (autopilot progress, warnings, draft actions)."""

    text: str
    level: str = "info"  # "info" | "warn" | "error"


@runtime_checkable
class EventSink(Protocol):
    """Anything that consumes handler Updates. The GUI implements this."""

    def emit(self, update) -> None: ...


class NullSink:
    """Default sink: drops everything. The CLI's console output is unchanged
    (handlers still print()), so until a real sink is installed nothing else
    happens — and tests that assert on stdout keep passing."""

    def emit(self, update) -> None:  # noqa: D401 - intentional no-op
        return None


@dataclass
class RecordingSink:
    """Collects Updates in order. Used by tests and as a simple GUI base."""

    updates: list = field(default_factory=list)

    def emit(self, update) -> None:
        self.updates.append(update)


# Module-level active sink (see module docstring for why it's not instance state).
_sink: EventSink = NullSink()


def set_sink(sink: EventSink) -> None:
    """Install the active event sink (the GUI calls this at startup)."""
    global _sink
    _sink = sink


def reset_sink() -> None:
    """Restore the no-op sink (tests + GUI teardown)."""
    global _sink
    _sink = NullSink()


def emit(update) -> None:
    """Send an Update to the active sink.

    A sink is best-effort UI: a broken sink must never crash an LCU handler, so
    we swallow and report rather than propagate (decide-or-fail: the failure is
    surfaced on stderr, the watcher keeps running).
    """
    try:
        _sink.emit(update)
    except Exception as exc:  # a GUI bug must not kill the watcher
        import sys

        print(f"(warn) event sink raised: {exc}", file=sys.stderr)
