from __future__ import annotations

from src.display import opgg_runes

# --------------------------------------------------------------------------- #
# GUI champion search (pick/ban pre-configuration)
#
# Headless, framework-agnostic helpers a GUI calls when the user types a
# champion name to *pre-configure* their draft: which champions to pick per
# lane and which to ban. The GUI feeds the chosen names into make_autopilot,
# which arms state.AUTOPILOT; the existing run_draft then auto-bans/picks when
# it's the user's turn. There is deliberately no immediate pick/ban here — the
# user selects ahead of time and the autopilot acts (the confirmed model).
#
# Decide-or-fail: an unknown/blank name is a raised, catchable error the GUI
# surfaces; we never resolve to a champion the user didn't ask for. No Qt here.
# --------------------------------------------------------------------------- #


class ChampionNotFoundError(Exception):
    """Raised when a typed name resolves to no champion."""


def resolve_champion(name: str) -> tuple[int, str]:
    """Champion name -> (id, display name) via opgg_runes' Data Dragon index.

    Raises ChampionNotFoundError on an unknown/blank name so the GUI can show it,
    rather than guessing a champion. Suitable as make_autopilot's `resolve`.
    """
    if opgg_runes is None:
        raise ChampionNotFoundError("Champion lookup unavailable (opgg_runes not importable).")
    if not (name or "").strip():
        raise ChampionNotFoundError("Type a champion name to search.")
    try:
        champ = opgg_runes.resolve_champion(name)
    except Exception as exc:  # opgg_runes raises on no match / fetch failure
        raise ChampionNotFoundError(f"No champion matched {name!r}: {exc}") from exc
    return champ["id"], champ["name"]


def search_champions(query: str, limit: int = 25) -> list[tuple[int, str]]:
    """All champions whose name contains `query` (case-insensitive), id+name.

    Powers the GUI's live search list. Empty query -> the whole sorted index, so
    the user can browse. Returns [] (not an error) when nothing matches — a search
    box legitimately shows "no results" while typing.
    """
    if opgg_runes is None:
        return []
    needle = (query or "").strip().lower()
    try:
        entries = opgg_runes.champion_index().values()
    except Exception:  # network/parse failure -> empty list, GUI shows "no results"
        return []
    matches = [
        (e["id"], e["name"])
        for e in entries
        if not needle or needle in e["name"].lower()
    ]
    matches.sort(key=lambda pair: pair[1])
    return matches[:limit]
