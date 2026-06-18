"""Persist the GUI's draft config (picks + bans) between sessions.

Qt-free on purpose: just JSON on disk under the same cache folder opgg_runes
uses (``resolve_cache_dir``), so it's unit-testable without a display. The window
loads this on startup and saves it whenever the config changes. Reads and writes
are best-effort -- a missing or corrupt file degrades to defaults rather than
raising, so a bad save can never stop the app from opening.
"""

from __future__ import annotations

import json
import os

from opgg_runes import resolve_cache_dir

CONFIG_FILE = "gui_config.json"
_MODES = ("solo", "flex", "aram")

# A draft config must be complete before it can be armed (or watched). A party of
# fewer than 5 gets a first + second role preference, and each role needs a
# primary + backup champion (the first may be banned/taken), so we require at
# least two positions, each with two champions. That also guarantees the second
# role preference is always a real lane -- never FILL. Bans are required too.
MIN_LANES = 2
CHAMPS_PER_LANE = 2
MIN_BANS = 2


def validation_error(
    lane_choices: dict[str, list[tuple[int, str]]],
    ban_choices: list[tuple[int, str]],
) -> str | None:
    """Why this config can't be armed, as a user-facing message, or None if valid."""
    partial = [lane for lane, ch in lane_choices.items() if 0 < len(ch) < CHAMPS_PER_LANE]
    if partial:
        return f"These positions need {CHAMPS_PER_LANE} champions: {', '.join(partial)}."
    full = [lane for lane, ch in lane_choices.items() if len(ch) == CHAMPS_PER_LANE]
    if len(full) < MIN_LANES:
        return f"Configure at least {MIN_LANES} positions, each with {CHAMPS_PER_LANE} champions."
    if len(ban_choices) < MIN_BANS:
        return f"Add at least {MIN_BANS} bans."
    return None


def config_path() -> str:
    return os.path.join(resolve_cache_dir(), CONFIG_FILE)


def load_config() -> dict:
    """The saved config dict, or {} if absent/unreadable."""
    try:
        with open(config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config: dict) -> None:
    """Write the config to the cache folder (best-effort; never raises)."""
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
    except OSError:
        pass


def _pairs(items) -> list[tuple[int, str]]:
    """Coerce a JSON list of [id, name] into clean (int, str) tuples, skipping junk."""
    out: list[tuple[int, str]] = []
    for item in items or []:
        try:
            cid, name = item
            cid, name = int(cid), str(name)
        except (TypeError, ValueError):
            continue
        if name:
            out.append((cid, name))
    return out


def normalize(data: dict, lanes: tuple[str, ...]) -> dict:
    """Coerce a loaded (untrusted) config to known lanes + (id, name) tuples.

    Returns {"lanes": {lane: [(id, name), ...]}, "bans": [(id, name), ...],
    "mode": str, "auto_start": bool}. Unknown lanes are dropped, malformed
    entries skipped, and mode/auto_start fall back to safe defaults.
    """
    raw_lanes = data.get("lanes") if isinstance(data.get("lanes"), dict) else {}
    lane_choices = {lane: _pairs(raw_lanes.get(lane)) for lane in lanes}
    mode = data.get("mode") if data.get("mode") in _MODES else _MODES[0]
    return {
        "lanes": lane_choices,
        "bans": _pairs(data.get("bans")),
        "mode": mode,
        "auto_start": bool(data.get("auto_start", True)),
    }


def serialize(
    lane_choices: dict[str, list[tuple[int, str]]],
    ban_choices: list[tuple[int, str]],
    mode: str,
    auto_start: bool,
) -> dict:
    """Build the JSON-serializable config dict from the window's current state."""
    return {
        "lanes": {lane: [list(c) for c in choices] for lane, choices in lane_choices.items()},
        "bans": [list(c) for c in ban_choices],
        "mode": mode,
        "auto_start": auto_start,
    }
