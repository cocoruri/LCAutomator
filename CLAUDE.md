# CLAUDE.md

Guidance for working in this repo. Read this before changing behavior.

## What this is

Two Python tools around League of Legends, no Riot API key required:

- **`opgg_runes.py`** — fetches OP.GG's recommended runes + summoner spells for a
  champion. Standard-library only (blocking `urllib`, on-disk cache under `.cache/`).
- **`lcu_watch.py`** — watches the local **LCU API** (League Client Update API) over
  `lcu-driver` and optionally automates champ select: auto-accept the ready check,
  auto-ban/pick in ranked, apply runes + spells on lock-in, and start matchmaking.

The real implementation lives in the **`src/` package** (see below). `lcu_watch.py`
at the repo root is a thin backward-compatibility **shim**, and `opgg_runes.py` is a
standalone module the package reuses.

## Architecture

```
lcu_watch.py        # shim: re-exports src/* as `lcu_watch.X`; CLI entry (`main`)
opgg_runes.py       # standalone OP.GG/Data Dragon fetcher (no src/ deps)
src/
  __init__.py       # explicit package (required so PyInstaller bundles src/)
  constants.py      # ALL tunables, enums, queue/lane tables, protocol strings
  endpoints.py      # LCU REST paths in one declarative place
  http.py           # ok(status) -> 2xx check
  state.py          # module-level live-session state (STATE, AUTOPILOT, name caches)
  champ_select.py   # pure reads over a champ-select session dict
  display.py        # name lookups + console rendering; owns the opgg_runes import
  build.py          # apply runes/spells; OP.GG mode mapping
  lobby.py          # plain lobby/queue read queries (GUI-reusable)
  autopilot.py      # queue setup + ranked draft (ban/pick) logic
  handlers.py       # lcu-driver event handlers; wires everything together
  cli.py            # argparse + main()
```

Single live client is assumed; shared per-session state is module-level in
`state.py` (the async event handlers are free functions with no instance to hang
state off). `STATE.reset()` clears it when champ select ends.

### The shim (`lcu_watch.py`)

Tests and any embedder reach everything as `lcu_watch.X`. The shim resolves **both
reads and writes** of `X` through a single `_owner(name)` lookup over an ordered
`_SOURCES` tuple, so a read and a write of the same name always hit the same
module — there is no hand-maintained forwarding list to drift. The defining module
must come before any module that imports the name (so the owner is the module the
running code actually reads); that's why `http/state/build/constants` lead the tuple.

Consequence: **runtime toggles work.** `lcu_watch.AUTO_APPLY = False` lands on
`src.constants`, and `handlers.py`/`build.py` read `constants.AUTO_ACCEPT`,
`constants.AUTO_APPLY`, `constants.REGION` **live at call time** (never imported
by value). If you add a rebindable name, make sure the code that reads it reads it
from its owning module at call time, not via a by-value import.

## Core principle: decide or fail — never guess

Every action against the client must be **strictly one of two outcomes**:

1. **Done deterministically** — we know exactly what to do and do it, or
2. **A surfaced error** the user can see/catch — either a catchable exception or a
   `(warn) ...` line plus a clean early return.

Do **not** invent a plausible value, pick a heuristic, or "do something close" when
the truthful answer is unknown. Doing work the user didn't ask for is worse than
stopping and telling them. Examples of this rule in the code:

- `setup_queue` raises `QueueNotAvailableError` when no lobby can be created, rather
  than silently picking a different queue. The caller catches it and prints it.
- `set_runes` edits the **currently-active** rune page in place; if there is no
  active page, or it isn't editable, it warns and leaves your runes untouched — it
  does not create/guess a page or delete the user's pages.
- `member_names` degrades to `{}` on failure (names are cosmetic); callers fall back
  to `"?"`. It never maps a puuid to a fabricated name.
- We removed prior guesses on purpose: a lobby member count that defaulted to `1`,
  and an `ownedPageCount` that defaulted to `2` before deleting pages.

Pick the tier by blast radius: **catchable exception** for control-flow-critical
failures the user must act on; **`(warn)` + early return** for cosmetic/best-effort
paths (name/asset lookups, rendering). Either way the outcome is visible — nothing
fails silently into wrong behavior.

## When you don't know the LCU: ask for the spec

The LCU API is local and only semi-documented. **The user has the full LCU API
spec and can search it.** If a task needs an endpoint, request/response shape, field
name, or enum value you are not certain about, **stop and ask the user to look it
up** rather than guessing a path or payload. Ask for something specific, e.g.:

- "Is there a batch endpoint that takes a list of puuids and returns names?"
- "Does `POST /lol-perks/v1/pages` set the new page current, or is a second call
  needed?"
- "What are the possible values of `gameflow-phase`?"

Decisions already grounded this way (don't re-guess them):

- Names: `POST /lol-summoner/v2/summoners/puuid` with a JSON list of puuids returns
  a summoner DTO per puuid; the name is `gameName` (the legacy `displayName` /
  `summonerName` are no longer populated, so don't add them back as fallbacks).
- Runes: rune pages carry a `current: bool`; we PUT the build onto the current page
  (`/lol-perks/v1/pages/{id}`) instead of create-then-activate.
- `/lol-gameflow/v1/session` includes `phase` (the `gameflow-phase` enum) alongside
  game mode.

Also prefer **fewer round-trips**: if the spec offers a single endpoint that returns
what we want (or a batch form), use it instead of N calls or a re-fetch.

## CLI today, library for a GUI tomorrow

The only front end right now is `src/cli.py` (argparse → `main`). But `src/` is
meant to be the **reusable library a GUI will sit on later** (see `TODO.md` item 2).
Keep that in mind:

- Put logic in the focused modules; keep `cli.py` to argument parsing and `handlers.py`
  to event wiring + console output. A GUI should be able to import `lobby.py`,
  `build.py`, `autopilot.py`, `champ_select.py` without dragging in CLI/printing.
- Read-query helpers (e.g. `lobby.fetch_available_queues`, `lobby.get_lobby_members`,
  `handlers.member_names`) are deliberately plain reads usable by a queue-selector or
  party view; their docstrings note the GUI intent — preserve that.
- Console output uses **plain ASCII** (no `✓`/unicode) so it survives a non-UTF-8
  Windows console; a GUI will render its own way regardless.

## Packaging (PyInstaller)

Distribution is a PyInstaller build, not a wheel. `lcu_watch.spec` analyzes
`lcu_watch.py` and statically follows its imports, so:

- `src/` **must stay a real package** (`src/__init__.py` present). PyInstaller can
  miss a namespace package, leaving the modules out of the frozen exe.
- Keep `src/` imports explicit/static (as the shim does). Don't introduce dynamic
  `importlib`-style loading of `src.*` without adding it to the spec `hiddenimports`.
- Build: `pyinstaller lcu_watch.spec` → `dist/lcu_watch/`.
- Data location: `opgg_runes.resolve_cache_dir()` caches under `~/.cache/lcu_automator`,
  or in a `.cache/` next to the app when a `.portable` marker file is present
  (`_app_dir()` resolves the PyInstaller exe dir when frozen, else the script dir).

## Commands

```bash
pip install -r requirements.txt        # runtime (lcu-driver)
pip install -r requirements-dev.txt    # + pytest
python -m pytest                       # full suite (no live client needed)
python -m ruff check src lcu_watch.py tests   # lint (keep clean)
pyinstaller lcu_watch.spec             # build the frozen exe
```

## Testing conventions

- Tests live in `tests/`, driven without `pytest-asyncio`: the `run` fixture is
  `asyncio.run`, and `conftest.py` provides `FakeConnection`/`FakeResponse` that
  record every `(method, endpoint, body)` and answer per-endpoint via a handler.
- The autouse `reset_lcu_state` fixture clears module-level state between tests —
  rely on it; don't leak `STATE`/`AUTOPILOT`/name caches.
- Assert on **behavior** (the recorded HTTP calls, printed output), not internals.
- Everything is referenced through the shim as `lcu_watch.X`. To intercept a name
  the running code calls as a module-global (e.g. `handlers` calling `apply_build`),
  patch the **owning module** (`monkeypatch.setattr(handlers, "apply_build", ...)`),
  not the shim.
- New behavior gets a test, including the failure/degradation path. Keep the suite
  green and ruff clean before finishing.

## Conventions

- `from __future__ import annotations` at the top of every module.
- All tunables/enums/protocol strings live in `src/constants.py`; all LCU paths in
  `src/endpoints.py`. Don't scatter literals at call sites.
- Comments are terse and explain *why*, matching the surrounding density.
- Modern Riot ID only: a player's name is `gameName`. Don't reintroduce legacy
  name fields or other dead fallbacks.
