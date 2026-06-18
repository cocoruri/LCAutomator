1.
- [x] Change where data is stored, if file `.portable` is present alongside the executable path (be it the compiled .exe or the .py script) we create the .cache folder and use it. If not we use the user's home folder (`C:/Users/<username>/.cache/lcu_automator`)
  - Implemented in `opgg_runes.resolve_cache_dir()` (marker = `.portable`, app name = `lcu_automator`).

---
2.
- [ ] Make GUI to hook into `lcu_watch.py`
