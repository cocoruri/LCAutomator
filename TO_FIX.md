# TO_FIX

An objective, prioritized review of `opgg_runes.py` and `lcu_watch.py`, focused on
maintainability, best practices, dead/leftover code, magic numbers, and
consolidating scattered state. Each item notes file:line and a suggested fix.

Ordering: **P1** = needed fixes (correctness, leftover code, deprecations) →
**P2** = maintainability / best practices → **P3** = project hygiene / nice-to-have.

---

## P1 — Needed fixes

### 1. Remove the leftover DEBUG lobby handler
`lcu_watch.py:744-753` (`on_lobby_debug`) is explicitly marked *"DEBUG (remove
later) … delete this whole handler to remove."* Its purpose — reading off the
real ARAM Mayhem queue id — is already done: `ARAM_MAYHEM_QUEUE_ID = 2400` and
`QUEUE_NAMES` capture it. It also relies on a hacky mutable function attribute
(`on_lobby_debug._last`).
**Fix:** delete the handler.

### 2. Replace deprecated `asyncio.get_event_loop()`
`lcu_watch.py:391` calls `asyncio.get_event_loop().run_in_executor(...)` from
inside a running coroutine. `get_event_loop()` is deprecated for this use since
Python 3.10 and emits a `DeprecationWarning`.
**Fix:** use `asyncio.get_running_loop().run_in_executor(...)`.

### 3. Trim the unused `SummonerSpell` enum members
`lcu_watch.py:66-76` defines 11 spell ids, but only `FLASH`, `GHOST`, and
`SMITE` are ever referenced (in `D_SLOT_SPELLS` / `F_SLOT_SPELLS`). The docstring
claims they're *"usable by name in the rules below,"* but no rules use them.
**Fix:** either remove the unused members, or keep the full enum but correct the
docstring so it isn't misleading (it reads as if they're wired in).

### 4. Don't assign a `lambda` to a variable
`opgg_runes.py:281` `tree = lambda i: RUNE_TREES.get(i, f"#{i}")` violates PEP 8
(E731).
**Fix:** make it a small `def`, or inline `RUNE_TREES.get(i, f"#{i}")`.

### 5. Redundant f-string
`opgg_runes.py:287` `f"  Shards            : "` has no placeholders, so the `f`
prefix is dead.
**Fix:** drop the `f`.

### 6. Cosmetic double-space in generated rune-page names
`lcu_watch.py:400` builds the name as `f"{PAGE_PREFIX} {name} ..."` while
`PAGE_PREFIX = "AUTO - "` already ends in a space (`lcu_watch.py:91`), producing
`"AUTO -  Jinx adc"`. Cleanup still works because `startswith(PAGE_PREFIX)`
matches, but the displayed name is sloppy.
**Fix:** `f"{PAGE_PREFIX}{name} {build.position}"`.

---

## P2 — Maintainability & best practices

### 7. Consolidate the champ-select state globals into one object
`lcu_watch.py:157-165` declares a sprawl of module-level mutable state
(`_last_snapshot`, `_applied_for`, `_handled_actions`, `_action_attempts`, plus
the `_champ_names`/`_spell_names` maps and `AUTOPILOT`). This forces `global`
declarations in multiple handlers (`lcu_watch.py:758, 788`) and a manual,
error-prone multi-line reset in `on_champ_select_end` (`lcu_watch.py:789-792`).
**Fix:** group the per-session bits into a small dataclass, e.g.

```python
@dataclass
class ChampSelectState:
    last_snapshot: tuple | None = None
    applied_for: tuple | None = None
    handled_actions: set[int] = field(default_factory=set)
    action_attempts: dict[int, int] = field(default_factory=dict)

    def reset(self) -> None:
        self.__init__()
```

One `STATE = ChampSelectState()` instance removes the `global` statements and
makes the reset a single `STATE.reset()` call. (This is the clearest "merge
loose variables into a structure" win in the codebase.)

### 8. Consolidate the queue id / name / fallback tables
`lcu_watch.py:115-126` spreads related queue facts across four names:
`QUEUE_IDS`, `ARAM_MAYHEM_QUEUE_ID`, `ARAM_FALLBACK_QUEUE_ID`, and `QUEUE_NAMES`.
The relationship (mode → queue id → display name) is implicit and duplicated
(e.g. 2400 appears as a constant *and* as a `QUEUE_NAMES` key).
**Fix:** a single table keyed by queue id, e.g.
`QUEUES = {420: "SoloQ", 440: "Flex", 450: "ARAM", 2400: "ARAM Mayhem", 1750: "Arena"}`
plus a small `MODE_QUEUES = {"solo": [420], "flex": [440], "aram": [2400, 450]}`,
then derive `queue_label` from the one table.

### 9. De-duplicate the lane-label mappings
The LCU↔display lane naming lives in at least three places:
- `LCU_TO_OPGG` (`lcu_watch.py:99-105`)
- the inline `{"utility": "support", "bottom": "adc", "middle": "mid"}` in
  `player_line` (`lcu_watch.py:221`) — a partial inverse of the above
- `LANE_ALIASES` / `POSITION_ORDER` (`lcu_watch.py:129-137`) and `POSITIONS`
  (`opgg_runes.py:52`)

The inline dict in `player_line` duplicates knowledge already in `LCU_TO_OPGG`.
**Fix:** derive the short display label from `LCU_TO_OPGG` (it already maps
middle→mid, bottom→adc, utility→support) instead of re-stating it inline.

### 10. Use the `ok()` helper consistently for HTTP status checks
`lcu_watch.py` defines `ok(status)` (`:276`) but several callers still hardcode
`resp.status == 200` / `!= 200`: lines `362, 433, 447, 703, 715`. The mix makes
it easy to forget that the LCU returns 200/201/204 interchangeably.
**Fix:** route all status checks through `ok()` (these particular GETs do return
200, so this is consistency, not a bug fix — but it removes a magic literal).

### 11. Replace remaining magic numbers with named constants
- `lcu_watch.py:294` `inv.get("ownedPageCount", 2)` — the default page count `2`
  is unexplained. Name it (e.g. `DEFAULT_OWNED_PAGES = 2`).
- `lcu_watch.py:261` `POSITION_ORDER.get(..., 9)` — the sort-last sentinel `9`
  is magic; a named `UNRANKED_SORT_KEY` or `len(POSITION_ORDER)` reads better.
- `opgg_runes.py:73` `timeout=20` — promote to a module constant
  (`HTTP_TIMEOUT = 20`) so all requests share one tunable value.
- `opgg_runes.py:265-266` the comment "4 primary + 2 secondary + 3 shards"
  encodes a layout the code doesn't enforce — fine as a comment, just be aware
  it's the only documentation of that shape.

### 12. Unify the "unknown value" sentinels
Placeholder strings for missing data are inconsistent: `"?"`, `"-"`,
`f"Champion#{id}"`, `f"Spell#{id}"`, `f"#{perk_id}"`, and `champ_name` returns
`None` while `spell_name` returns `"-"` for the same "nothing selected" case
(`lcu_watch.py:171-181`). This makes call sites guess the convention.
**Fix:** document/standardize the convention (e.g. always return a string
placeholder, or always return `None` and format at the edge).

### 13. Narrow or annotate broad `except Exception`
`lcu_watch.py:190, 200` (`load_static`) and `lcu_watch.py:395` (`apply_build`)
catch bare `Exception`. The import guard at `lcu_watch.py:82` is acceptable (it
has a `pragma: no cover` and degrades gracefully), but the others can mask real
bugs.
**Fix:** catch the expected types (e.g. `aiohttp`/network + `KeyError`), or at
least keep the broad catch but ensure the message includes enough context.

### 14. Loosen the over-strict `get_json` return type
`opgg_runes.py:71` annotates `-> object`, but every caller subscripts the result
(`get_json(url)["data"]`, `get_json(...)[0]`), which a type checker rejects on
`object`.
**Fix:** annotate `-> Any` (from `typing`) to match actual usage.

### 15. Clarify the "backwards-compatible" helper
`opgg_runes.py:352-357` `fetch_runes` is labelled a *"backwards-compatible
helper,"* but this is a standalone script with no prior published API — the only
caller is the CLI (`opgg_runes.py:443`).
**Fix:** drop the misleading comment (it's just a convenience wrapper), or inline
it if the CLI is its sole user.

### 16. Note the inconsistent cache-invalidation strategy
`champion_meta` refreshes daily via `_cached_today` (`opgg_runes.py:214`), while
`champion_index` and `perk_names` cache forever and only refresh on a lookup
miss (`opgg_runes.py:135-161`). This is a reasonable design choice, but it's
undocumented and surprising.
**Fix:** add a one-line comment explaining why meta is time-based and the others
are miss-based (or align them).

### 17. Consider `logging` over `print` for diagnostics
Both files emit operational output, warnings (`(warn) …`), and errors
(`(error) …`) via `print`. For a watcher that runs for a long time, the standard
`logging` module would separate user-facing status from diagnostics and add
timestamps/levels for free.
**Fix:** optional — migrate `(warn)`/`(error)` lines to `logging`; keep plain
`print` for the intended user output if preferred.

---

## P3 — Project hygiene

### 18. Add a `requirements.txt` (or `pyproject.toml`)
`lcu_watch.py` depends on the third-party `lcu_driver` (`lcu_watch.py:56`), which
is declared nowhere. A fresh checkout can't be set up reproducibly.
**Fix:** pin dependencies in `requirements.txt` (at minimum `lcu-driver`).
`opgg_runes.py` is stdlib-only — worth stating that explicitly.

### 19. Add a `.gitignore` and a README
The repo isn't under version control yet (no git repo). Before `git init`:
- ignore `__pycache__/` and the generated `.cache/` (champions/perks/meta JSON).
- a short README documenting the two entry points, the `AUTO_ACCEPT` /
  `AUTO_APPLY` / `REGION` toggles (`lcu_watch.py:88-91`), and the CLI examples
  already in the module docstrings.

### 20. No tests
There is no test coverage. The pure functions are very testable without a live
client: `arrange_spells`, `opgg_mode_for`, `unavailable_champions`,
`queue_label`, `_slug`/`_lookup`/`resolve_champion`, and `RunePage.from_opgg` /
`to_lcu_page`.
**Fix:** add a minimal `pytest` suite around these, since they encode the draft
and build-mapping rules most likely to break on a patch.
