# TO_FIX

## Bugs / Correctness

- [x] **#1** `opgg_runes.py` `_lookup` (line ~181): prefix match is non-deterministic — dict iteration order decides which champion wins on ambiguous input (e.g. `"ka"` → Kalista vs Karma vs Karthus). Sort matches (e.g. by slug length then alpha) or reject ambiguous prefixes.
- [x] **#2** `lcu_watch.py` slot-freeing loop (line ~332): frees until `len(pages) < owned`, then immediately POSTs one more — can exceed the limit by one. Also no user-facing message when all pages are non-deletable and the POST will fail.
- [x] **#3** `lcu_watch.py` broad `except Exception` around `import opgg_runes` (line ~84) and cosmetic-name loaders (lines ~229, ~239): silently degrades to `opgg_runes = None` even on programming errors (SyntaxError, AttributeError). At minimum log the caught exception.
- [x] **#4** `lcu_watch.py` `arrange_spells` (line ~353): when both spells are D-forced (e.g. Flash + Ghost), both map to slot 0 with no warning. Document or guard the impossible pair.

## Hardcoded Strings

- [x] **#5** `opgg_runes.py` line ~130: Data Dragon host `https://ddragon.leagueoflegends.com` and locale `en_US` are inline magic strings. Extract `DDRAGON_BASE` and `LOCALE` constants (locale matters for non-English users).
- [x] **#6** `opgg_runes.py`: default values `"global"` and `"ranked"` are repeated literally across 6+ function signatures and argparse defaults. Extract `DEFAULT_REGION` and `DEFAULT_MODE` constants so CLI and function defaults stay in sync.
- [x] **#7** `lcu_watch.py`: LCU endpoint paths are inline strings repeated N times (`/lol-perks/v1/pages` ×4, `/lol-lobby/v2/lobby` ×5, etc.). Collect into module-level constants or an `Endpoints` class; same strings appear independently in the test file.
- [x] **#8** `lcu_watch.py`: game-protocol magic strings scattered throughout — `"GAME_STARTING"`, `"PLANNING"`, `"InProgress"`, `"None"`, `"ARAM"`, `"CLASSIC"`, `"BAN_PICK"`, `"mayhem"`, `"Available"`. Replace with named constants or an enum.
- [x] **#9** `lcu_watch.py` line ~498: role-preference sentinels `"UNSELECTED"` and `"FILL"` are inline magic strings. Extract constants.

## Hardcoded Numbers

- [x] **#10** `opgg_runes.py` line ~347: HTTP status codes `404` and `422` are bare integers. Reference `http.HTTPStatus` or define named constants.
- [x] **#11** `lcu_watch.py` line ~498: `5` as the full-team-size threshold is a magic number. Extract `FULL_TEAM_SIZE = 5`.
- [x] **#12** Tests: queue IDs `420`, `440`, `2400`, `450` are duplicated between the source `QUEUES` dict and test files. Tests should reference the source constants.

## Structure / Maintainability

- [x] **#13** `opgg_runes.py` lines ~144–228: `champion_index`, `perk_names`, and `champion_meta` share the same memory→cache→network pattern in near-identical code. Consolidate into a single parameterized cache helper.
- [x] **#14** `lcu_watch.py` `ChampSelectState.reset` (line ~185): calls `self.__init__()` to reset state — breaks under subclassing, confuses type checkers. Reassign fields explicitly or replace `STATE` with a fresh instance.
- [x] **#15** `lcu_watch.py`: module-level mutable globals `STATE`, `AUTOPILOT`, `_champ_names`, `_spell_names` are mutated from async handlers. Makes isolated testing hard (conftest must reset them) and would break under concurrent connections.
- [x] **#16** `opgg_runes.py` line ~135: `int(champ["key"])` and `p["stats"]["play"]` in fetch helpers have no defensive handling. A schema change at the data source produces an unhandled `KeyError`/`ValueError` that discards the whole index silently.
- [x] **#17** `opgg_runes.py` `get_json` (lines ~74–77): only `fetch_build` catches `HTTPError`; `URLError` (offline/DNS) and `json.JSONDecodeError` propagate raw from all callers.

## Project / Repo

- [x] **#18** `.cache/` JSON files (`champions.json`, `perks.json`, `meta.json`) are committed to the repo. These are regenerated runtime artifacts; `meta.json` goes stale daily. Add `.cache/` to `.gitignore`.
- [x] **#19** This file (`TO_FIX.md`) was empty — `[TO_FIX #N]` tags in the test files referenced a checklist that no longer existed.
