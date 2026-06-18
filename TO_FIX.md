# TO_FIX — Suggested additional tests

The `tests/` suite currently passes (65 tests) and covers the pure helpers well
(`_slug`, `_lookup`, `arrange_spells`, `unavailable_champions`, the `run_draft`
decision matrix, `RunePage` mapping, plus happy-path queue setup and
ready-check).

The gaps below are concentrated in three areas that carry the most risk: **code
that mutates the live client**, **the build / lane-selection fallback logic**,
and **error / retry paths**. Ordered by value. All fit the existing harness —
`FakeConnection(handler)` + the `run` fixture for async, and `monkeypatch` of
`get_json` / `best_build` for network.

---

## P1 — Untested logic that changes the client or encodes core rules

### 1. `set_runes` page management — `lcu_watch.py:281`
Entirely untested, and it *deletes and creates* rune pages. Drive with a
`FakeConnection` handler:
- deletes pre-existing `AUTO -` pages before creating the new one
- when at the `ownedPageCount` limit, deletes an editable page (prefers the
  `current` one) to make room
- never deletes a page with `isDeletable` false
- POSTs the page body, then PUTs `currentpage` to the created id
- warns (does not raise) on a non-2xx POST

### 2. `best_build` ranked lane selection + fallback — `opgg_runes.py:360`
Only the ARAM positionless path is tested. Untested:
- `preferred` lane is tried first
- falls back to `champion_positions` order when `preferred` misses
- dedupes already-`tried` lanes, then probes all `POSITIONS` keeping the
  most-played build
- raises `RuntimeError` when no position yields a build
- positionless `RuntimeError` when fetch returns `None` (`opgg_runes.py:387`)

### 3. `attempt_action` retry / give-up — `lcu_watch.py` (~613)
Resilience logic backed by `STATE.action_attempts`. Drive across repeated calls
with a failing-then-succeeding handler:
- success adds the id to `STATE.handled_actions`
- first failure warns and retries (id *not* marked handled)
- gives up after `MAX_ACTION_ATTEMPTS`, marking handled

### 4. `fetch_build` HTTP error branching — `opgg_runes.py:334`
- 404 / 422 → `None`
- any other `HTTPError` re-raises
- empty `summoner_spells` → `best_spells == []`
- positionless `label` is the mode, not the position

---

## P2 — Medium priority

### 5. `setup_queue` orchestration — `lcu_watch.py:479`
- `--no-start` sets roles only and never POSTs a lobby
- otherwise tries candidates until one is accepted
- all candidates rejected → gives up without starting the search
- success POSTs the matchmaking search

### 6. CLI validation paths (raise `SystemExit` — easy wins)
- `build_autopilot`: unknown lane (`lcu_watch.py:833`) and lane given twice
  (`lcu_watch.py:835`)
- `main` / `parse_args`: more than two `--lane` (`lcu_watch.py:901`)

### 7. Quick pure-helper wins
- `champ_name` / `spell_name`: `None` / `<=0` sentinels, known id, unknown id →
  `Champion#N` / `Spell#N` / `-`
- `summarize`: identical session → equal tuple; a changed pick / intent →
  different tuple (this drives the print-dedupe)

### 8. `set_role_prefs` remaining branches — `lcu_watch.py:500`
- the `"FILL"` fallback (small party, single configured lane)
- the ARAM / no-lane-order early return
- the warn-on-failure path
(Full-stack and two-pref small party are already covered.)

### 9. `resolve_member_name` fallback chain — `lcu_watch.py`
Direct `gameName`; puuid lookup; `summonerId` fallback; `"?"` when all fail.

### 10. `current_game_mode` / `lobby_member_count` parsing
- queue vs map `gameMode` fallback; non-200 → `""`
- `members` length vs the `or 1` fallback on empty / missing members

### 11. Reference-data caching — `opgg_runes.py`
- `_load_cache` / `_save_cache` round-trip and OSError tolerance
- `champion_index` / `perk_names` memory → cache → network ordering
- `perk_name` refresh-once-on-miss
- JSON string → int key restoration for perks

---

## P3 — Lower priority

### 12. `set_spells`
`<2` spells is a no-op; correct PATCH body; warn on failure.

### 13. `opgg_runes.main()` CLI
Exit codes (unknown champion → 1 on stderr; "no rune data" → 1); `--json` /
`--all` output shape; auto-detect vs explicit `--position`.

### 14. `ChampSelectState.reset()` / `on_champ_select_end`
Clears `handled_actions` and `action_attempts` (regression guard for the
consolidated state object).

### 15. `apply_build` orchestration
Unsupported mode leaves setup alone; ranked passes the mapped `preferred` lane;
a fetch exception is caught and warned (monkeypatch `opgg_runes.best_build` +
`FakeConnection`).

---

**Highest risk-reduction per test:** #1 (`set_runes`) and #3 (`attempt_action`)
— both mutate state / the client and currently have zero coverage.
