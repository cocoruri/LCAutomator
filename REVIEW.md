# Code Review: Modularization of `lcu_watch.py`

## Summary

This change refactors a previously monolithic `lcu_watch.py` into a `src/` package (11 modules) while keeping `lcu_watch.py` as a thin backward-compatibility shim that re-exports everything tests reference as `lcu_watch.X`. The test suite (`tests/test_lcu_watch.py`) was expanded and updated to match the new in-place rune-page flow. A `TODO.md` was added with two planned features.

Overall the decomposition is well-conceived: modules have clear, single responsibilities (`http`, `champ_select`, `display`, `build`, `lobby`, `autopilot`, `handlers`, `cli`, `state`, `constants`, `endpoints`), constants are centralized, and the existing behavior appears preserved. The test coverage is genuinely good — async flows, retry/give-up logic, CLI validation, and degradation paths are all exercised.

The most serious concern is the `_ShimModule` metaprogramming facade, which is clever but fragile and carries a real correctness footgun (the `_STATE_FORWARDS`/`_BUILD_FORWARDS` lists are hand-maintained and silently wrong if drifted). There is also a missing `src/__init__.py`, several documentation drifts, and a few minor robustness gaps.

Files reviewed:
- `lcu_watch.py`
- `tests/test_lcu_watch.py`
- `TODO.md`
- `src/*.py` (all 11 modules)
- `tests/conftest.py` (context)
- `opgg_runes.py` (context)

---

## Critical

None. The code is functional and the test suite passing implies the shim resolves correctly for current usage.

---

## High

### H1. Missing `src/__init__.py` — package relies on implicit/namespace-package behavior
`src/` has no `__init__.py` (only `.py` modules and a `__pycache__` exist). The code imports `from src import autopilot`, `from src.constants import *`, etc. This works today only because:
- the repo root is on `sys.path` (cwd or the `conftest.py` `sys.path.insert`), and
- Python 3.3+ treats `src/` as an implicit namespace package.

This is brittle. It breaks if the project is ever installed as a wheel, run from a different cwd, or if another `src` namespace appears on the path. Given commit `0a9518f "Add packaging metadata"`, the package is intended to be distributable, where namespace-package behavior for a top-level `src` is a common source of import failures. Add an explicit `src/__init__.py` (even if empty) and confirm the packaging config (setuptools `packages`/`find`) actually includes it.

### H2. The `_ShimModule` forwarding lists are hand-maintained and fail silently when wrong
`lcu_watch.py:68-69`:
```python
_STATE_FORWARDS = {"AUTOPILOT", "STATE", "_champ_names", "_spell_names"}
_BUILD_FORWARDS = {"current_game_mode", "set_runes", "set_spells"}
```
These sets encode a contract: any name that is *rebindable* (monkeypatched in a test, or mutated at runtime like `state.AUTOPILOT`) must be forwarded to the *owning* module, otherwise reads and writes diverge.

The footgun: if a test does `lcu_watch.X = ...` for an `X` not in either forward set, `__setattr__` falls through to `super().__setattr__`, which sets the attribute *on the shim only*. Subsequent code inside `src/` reading the real module name will not see it, and the failure is silent (no error, just stale behavior). This is exactly the class of bug that is hard to diagnose.

Concretely, the correctness depends on subtle facts:
- `monkeypatch.setattr(lcu_watch, "current_game_mode", fake_mode)` works *only* because `current_game_mode` is in `_BUILD_FORWARDS` and `apply_build` calls it as a module-global in `build.py`. Remove it from the set and the test passes against a stale shim attribute while the real code calls the un-patched function — a false green.
- `AUTO_ACCEPT` / `AUTO_APPLY` are *not* forwarded. They live in `constants.py` and are imported by-value into `handlers.py` (`from src.constants import AUTO_ACCEPT, AUTO_APPLY`). Setting `lcu_watch.AUTO_APPLY = False` (as the docstring at line 29 instructs users to do "below") would set it on the shim and have **no effect** on the handler's behavior. The documented toggle is effectively dead through this entry point.

Recommendation: either (a) collapse the read/write forwarding to a single source-of-truth resolution that uses the same module for both get and set (so they can never diverge), or (b) add a guard in `__setattr__` that raises on assignment to a name owned by a `_READ_MODULES` member but absent from the forward sets, turning silent drift into a loud failure. At minimum, document the AUTO_ACCEPT/AUTO_APPLY caveat.

---

## Medium

### M1. Documentation drift: module docstring still describes the old create-page rune flow
`lcu_watch.py:19-22` says:
```
  * runes  -> POST /lol-perks/v1/pages (a slot is freed first if you're full)
```
But `set_runes` in `src/build.py:23-44` was rewritten to **edit the active page in place** via `PUT /lol-perks/v1/pages/{id}` — no POST, no slot juggling. The tests confirm the new behavior (`test_set_runes_edits_the_active_page`, lines 364-370). The top-level docstring now actively misleads. Update lines 19-22 to describe the PUT-in-place flow.

### M2. Documentation drift: `RunePage.to_lcu_page` docstring says "POST"
`opgg_runes.py:359` — docstring "Body for POST /lol-perks/v1/pages on the League client." The body is now used for a PUT to an existing page id. The `"current": True` and `"name"` fields are vestigial for the edit-in-place path (the page is already current; renaming the user's active page is a behavior change worth noting). Not a bug, but worth a comment: editing the active page in place mutates the user's existing named page (renames it to `AUTO - ...`), which is a more invasive side effect than creating a scratch page. Confirm this is intended product behavior.

### M3. `AUTO_ACCEPT` / `AUTO_APPLY` / `REGION` are not runtime-configurable through the shim
Tied to H2. These constants are module-level booleans in `constants.py`, imported by-value. There is no test asserting `AUTO_ACCEPT = False` actually disables accepting, and no working runtime switch. If these are meant to be user-toggleable (the docstrings imply so), they should be read indirectly (e.g. `from src import constants` then `constants.AUTO_ACCEPT` at call time) so a single point of mutation works. As written they are effectively compile-time constants.

### M4. `member_names` can map a puuid to `None`
`src/handlers.py:69`:
```python
return {s["puuid"]: _display_name(s) for s in await resp.json() if s.get("puuid")}
```
`_display_name` returns `dto.get("gameName")`, which can be `None`. So the map can contain `{puuid: None}`. The caller at line 87 does `names.get(m.get("puuid")) or m.get("gameName") or "?"`, so the `None` is tolerated there. But the function's stated return type is `dict[str, str]` and a documented contract of "puuid -> name"; a `None` value violates it and would surprise a future GUI caller (the docstring explicitly anticipates GUI reuse). Filter out falsy names, or document that values may be `None`.

### M5. `local_pick` accumulates across all action groups without taking the last/locked one deterministically
`src/champ_select.py:6-15` iterates every group and every action, overwriting `champion_id` whenever it sees a matching pick action, and `locked = action.get("completed", False)`. If the actions list ever contained more than one pick action for the local cell (it shouldn't in normal play, but the structure allows it), the result depends on iteration order and the last-seen `completed` flag wins. This is currently correct for real sessions but is an implicit assumption. A short comment ("the local player has exactly one pick action") or an explicit "prefer completed" rule would make the invariant clear. Low likelihood, hence Medium.

---

## Low

### L1. `champ_name` redundant condition
`src/display.py:32`:
```python
if not champion_id or champion_id <= 0:
```
`not champion_id` already covers `0` and `None`. The remaining case `champion_id <= 0` only adds negative ids. Since `not (-1)` is `False`, the `<= 0` is needed for negatives — so it is *not* fully redundant, but the intent reads awkwardly. `if not champion_id or champion_id < 0:` is clearer (0 is handled by the first clause). Minor readability.

### L2. `arrange_spells` does not validate length / mixed F-slot conflicts
`src/build.py:47-68` handles the "both want D" conflict but not "both want F" (two F-forced spells — not currently possible since only SMITE is F-forced, but the asymmetry is implicit). Also it sorts the full list even if `len != 2`. Callers (`set_spells`) guard on `< 2`, so this is safe, but the function would silently reorder a 3-element list. A one-line assumption comment would help.

### L3. `print_lobby` mode label can mislabel a CLASSIC lobby
`src/handlers.py:79` uses `queue_label(cfg.get('queueId'), cfg.get('gameMode'))`. For an unknown queue id it falls back to `gameMode` (e.g. "CLASSIC") rather than a friendly name. Acceptable as a fallback, but `QUEUES` already maps 420/440 — fine. No action needed; noting for completeness.

### L4. Broad `except Exception` blocks
`src/display.py:54,62` and `src/build.py:134` catch bare `Exception`. For cosmetic name loading (display.py) this is justified and commented. For `apply_build` (build.py:134) catching everything around `best_build` is reasonable for network resilience, but it will also swallow programming errors (e.g. an `AttributeError` from a refactor) as "could not fetch build". Consider narrowing to the expected network/value error types, or at least logging `repr(exc)` with the type so a bug is distinguishable from a legitimate no-data condition.

### L5. Unicode glyphs in user-facing output on Windows
`src/handlers.py:94` prints `"✓ Connected..."`. On a Windows console with a non-UTF-8 code page (cp1252), printing `✓` can raise `UnicodeEncodeError` and crash the `on_ready` handler. Given the environment is Windows 10 / PowerShell, prefer an ASCII marker (e.g. `"Connected to the League client."`) or guard stdout encoding. The em-dashes/arrows elsewhere (`->`, `<--`) are ASCII and fine; the `✓` is the risk.

---

## Nits

- `src/display.py:84-91` — `summarize` returns a tuple including `local_pick(session)` (itself a tuple). Fine, just note it recomputes `local_pick` which `print_champ_select` also calls; negligible cost, but the value could be computed once and shared if churn ever matters.
- `src/http.py` — a one-function module (`ok`) is a thin file; reasonable for cohesion, but could live in a small `util` module. Stylistic only.
- `TODO.md` — item 1 describes a `.portable` data-location strategy and item 2 a GUI hook; both are clear. Consider noting that the GUI (item 2) is already partly designed-for in `lobby.py`/`member_names` docstrings, to keep intent discoverable.
- `src/cli.py:9` imports `opgg_runes` from `src.display`; sourcing the third-party module through the display module is indirect. A direct `import opgg_runes` (guarded) would be more obvious, though the current approach centralizes the import-failure handling, which is a defensible trade.
- `src/constants.py:30-33` — `from src.constants import *` in the shim (`lcu_watch.py:62`) re-exports these; combined with `# noqa: F401,F403` this is intentional but means tooling can't see which names are actually used. Acceptable given the documented test-compat goal.

---

## Test Quality Assessment

Strengths:
- Async paths driven cleanly via `asyncio.run` + `FakeConnection` without a `pytest-asyncio` dependency (`tests/conftest.py:60-63`).
- `reset_lcu_state` autouse fixture (`tests/conftest.py:66-73`) correctly clears module-global state between tests, which is essential given the shared `STATE`/`AUTOPILOT`/name caches.
- Good coverage of edge cases: retry/give-up (`test_attempt_action_*`), degradation on error (`test_member_names_degrades_on_error`, `test_apply_build_warns_on_fetch_error`), CLI validation `SystemExit` paths, queue fallback ordering, and the sentinel/fallback name logic.
- Behavior-focused assertions (recorded HTTP calls) rather than implementation internals.

Gaps:
- No test asserts that `AUTO_ACCEPT = False` / `AUTO_APPLY = False` actually disable the behavior (ties to H2/M3). Given the shim cannot currently toggle them, a test would surface the dead-switch bug.
- No test exercises the `_ShimModule.__setattr__` fall-through case (assigning a non-forwarded name) — the most fragile part of the new design is untested. A test that monkeypatches a forwarded vs non-forwarded name and asserts the owning module sees the change would lock the contract.
- `member_names` mapping a puuid to `None` (M4) is not asserted against; `test_member_names_resolves_in_one_request` only uses DTOs with real names.
- No test for the Unicode-print path (L5), though that is environment-specific.

---

## Recommended Priority

1. Add `src/__init__.py` and verify packaging includes the package (H1).
2. Harden the shim: make read/write resolve through one source so forward sets can't drift, or raise on un-forwarded assignment to an owned name; add a test (H2).
3. Fix or document the `AUTO_ACCEPT`/`AUTO_APPLY` runtime-toggle gap (M3/H2).
4. Update the stale rune-flow docstrings in `lcu_watch.py:19-22` and `opgg_runes.py:359` (M1/M2).
5. Address `member_names` `None` values (M4) and the Windows `✓` encoding risk (L5).
