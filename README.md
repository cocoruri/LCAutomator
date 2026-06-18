# League helper scripts

Two small scripts for League of Legends, built on Riot's local **LCU API** and
OP.GG's public build data. No API key required.

- **`opgg_runes.py`** — fetch OP.GG's recommended runes (and summoner spells)
  for a champion. Standard-library only.
- **`lcu_watch.py`** — watch the League client and optionally automate it:
  auto-accept the ready check, auto-ban/pick in ranked, set runes + summoner
  spells on lock-in, and (optionally) start matchmaking. Needs `lcu-driver`.

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

### Toggles (constants near the top of `lcu_watch.py`)

| Constant      | Default     | Effect |
|---------------|-------------|--------|
| `AUTO_ACCEPT` | `True`      | Accept the ready check when a match is found. |
| `AUTO_APPLY`  | `True`      | Set OP.GG runes + spells when you lock in. |
| `REGION`      | `"global"`  | OP.GG region for build lookups. |
| `PAGE_PREFIX` | `"AUTO - "` | Name prefix for rune pages this tool creates. |

Runes are applied for Summoner's Rift (ranked/normals) and ARAM; modes with no
OP.GG runes build (ARAM Mayhem, Arena, URF, ...) are left untouched.

## Tests

```bash
python -m pytest
```

Covers the pure build-mapping and draft/queue logic without a live client
(network and the LCU connection are stubbed).

## Notes

The LCU API is local and undocumented; Riot tolerates client tooling like this
but it isn't officially supported, and endpoints/queue ids can change between
patches.
