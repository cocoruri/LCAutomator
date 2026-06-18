# League Champ Select Automator

Automates the tedious parts of League of Legends champ select through Riot's local
**LCU API** (and OP.GG's public build data) — accept the ready check, ban/pick in
ranked, and apply OP.GG runes + summoner spells the moment you lock in. No Riot API
key required.

**Where this is going.** Today it's a command-line tool. The goal is a desktop
**GUI app for automating champ select**: pick your queue, lanes, champs, and bans in
a UI and let it drive the client. The automation logic already lives in a reusable
`src/` library (queue setup, draft rules, rune/spell application, lobby/queue reads)
so the planned GUI can sit directly on top of it, and the whole thing ships as a
standalone Windows executable built with **PyInstaller** — no Python install needed
by the end user.

The two pieces today:

- **`opgg_runes.py`** — fetches OP.GG's recommended runes and summoner spells for a
  champion. Standard-library only.
- **`lcu_watch.py`** — the CLI front end plus the `src/` automation library. Watches
  the client over `lcu-driver` and, by default, auto-accepts the ready check and
  applies runes/spells on lock-in; with `--mode` it also queues and auto-drafts.

## Design principles

- **Decide or fail — never guess.** Every action against the client is either done
  deterministically or surfaces a clear error (a catchable exception, or a `(warn)`
  line and a clean skip). It will not invent a value or "do something close" to what
  you asked — it stops and tells you instead.
- **Library first.** Behavior lives in small, GUI-reusable modules under `src/`; the
  CLI is just one front end.

See [`CLAUDE.md`](CLAUDE.md) for architecture and contributor conventions.

## Setup

```bash
pip install -r requirements.txt        # runtime (lcu-driver)
pip install -r requirements-dev.txt    # + pytest, to run the tests
```

`opgg_runes.py` caches champion/perk/meta data under `.cache/` on first run.

## opgg_runes.py

```bash
python opgg_runes.py "Jinx"                  # auto-detect main lane
python opgg_runes.py "Miss Fortune" --position adc
python opgg_runes.py Lee Sin --position jungle --all
python opgg_runes.py Ahri --json             # LCU-ready rune-page body
```

## lcu_watch.py

Run with no arguments to just watch (and, by default, auto-accept + auto-apply
runes). Pass `--mode` to also queue and auto-draft.

```bash
python lcu_watch.py                          # watch only
python lcu_watch.py --mode aram              # queue ARAM Mayhem
python lcu_watch.py --mode flex \
    --lane jungle Shaco Briar --lane middle Ahri Lux --ban "Lee Sin" Zed
# In a party you don't own: set roles/champs/bans but let the owner queue.
python lcu_watch.py --mode flex --lane jungle Shaco Briar --no-start
```

- `--mode {solo,flex,aram}` — `solo`/`flex` are ranked; `aram` targets ARAM
  Mayhem, falling back to plain ARAM if the event isn't live.
- `--lane POSITION CHAMP1 CHAMP2` — repeatable (max 2). First lane is your
  first role preference. Draft picks the first available champ for your
  assigned lane, else the second, else leaves it to you.
- `--ban CHAMP1 CHAMP2` — bans the first, or the second if the first is already
  banned.
- `--no-start` — configure roles only; don't create a lobby or start the queue.

### Runes on lock-in

When `AUTO_APPLY` is on, the OP.GG build is written onto your **currently-active
rune page, edited in place** (renamed to `AUTO - <champ> <lane>`). No page is
created or deleted; if there's no editable active page, your runes are left
untouched and you get a warning. Runes are applied for Summoner's Rift
(ranked/normals) and ARAM; modes with no OP.GG runes build (ARAM Mayhem, Arena,
URF, ...) are left alone.

### Toggles (`src/constants.py`)

| Constant      | Default     | Effect |
|---------------|-------------|--------|
| `AUTO_ACCEPT` | `True`      | Accept the ready check when a match is found. |
| `AUTO_APPLY`  | `True`      | Apply OP.GG runes + spells when you lock in. |
| `REGION`      | `"global"`  | OP.GG region for build lookups. |
| `PAGE_PREFIX` | `"AUTO - "` | Name prefix applied to your active rune page. |

These are read live, so they can also be toggled at runtime via the shim
(e.g. `lcu_watch.AUTO_APPLY = False`) — useful for an embedding GUI.

## Tests

```bash
python -m pytest
```

Covers the build-mapping, draft/queue, and event-handler logic without a live
client (network and the LCU connection are stubbed).

## Packaging

Built into a standalone executable with PyInstaller:

```bash
pyinstaller lcu_watch.spec        # -> dist/lcu_watch/
```

`src/` is a real package so PyInstaller bundles it from `lcu_watch.py`'s imports.

## Notes

The LCU API is local and only semi-documented; Riot tolerates client tooling like
this but it isn't officially supported, and endpoints/queue ids can change between
patches.
