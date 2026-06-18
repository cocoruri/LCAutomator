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
  * runes  -> PUT /lol-perks/v1/pages/{id} onto your currently-active rune page
              (edited in place; no page is created or deleted)
  * spells -> PATCH /lol-champ-select/v1/session/my-selection
It picks the OP.GG mode from the client's game mode: Summoner's Rift (ranked
solo/flex, normals) uses ranked data, ARAM uses ARAM data. ARAM Mayhem and other
modes with no OP.GG runes build are left untouched.
For ranked the lane comes from the client's assigned position, falling back to
the champion's most-played lane when none is assigned (normals/customs/practice
tool). Modes with no OP.GG runes build (Arena, URF, ...) are left untouched.
Set AUTO_APPLY = False in src/constants.py to watch without changing anything
(or toggle src.constants.AUTO_APPLY / .AUTO_ACCEPT at runtime).

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

import sys

from src import autopilot as _autopilot
from src import build as _build
from src import champ_select as _champ_select
from src import cli as _cli
from src import constants as _constants
from src import display as _display
from src import handlers as _handlers
from src import http as _http
from src import lobby as _lobby
from src import state as _state
from src.cli import main

# Every public name tests/users reach as `lcu_watch.X` is resolved from these
# source modules, in order. Both reads and writes go through the same lookup, so
# a monkeypatch or runtime toggle through the shim (e.g. `lcu_watch.AUTO_APPLY =
# False`) reaches the real module that owns X -- reads and writes can never
# diverge, and there is no hand-maintained forwarding list to drift out of sync.
#
# Order matters only when a name is defined in one module and imported into
# another: the *defining* module must come first so the owner is the module the
# running code actually reads. Hence http/state/build/constants lead.
_SOURCES = (
    _http,          # ok
    _state,         # STATE, AUTOPILOT, _champ_names, _spell_names, ChampSelectState
    _build,         # set_runes, set_spells, current_game_mode, apply_build, ...
    _constants,     # AUTO_ACCEPT, AUTO_APPLY, REGION, queue ids, SummonerSpell, ...
    _champ_select,
    _display,
    _lobby,
    _autopilot,
    _handlers,
    _cli,
)


def _owner(name):
    """The first source module that defines `name`, or None."""
    return next((m for m in _SOURCES if hasattr(m, name)), None)


class _ShimModule(sys.modules[__name__].__class__):
    def __getattr__(self, name):
        # __getattr__ only fires for names not already on the shim itself.
        if not name.startswith("__"):
            owner = _owner(name)
            if owner is not None:
                return getattr(owner, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        owner = None if name.startswith("__") else _owner(name)
        if owner is not None:
            setattr(owner, name, value)
        else:
            super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ShimModule


if __name__ == "__main__":
    main()
