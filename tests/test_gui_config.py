"""Persistence of the GUI draft config (Qt-free; no PySide6 needed)."""

from gui import config

_LANES = ("top", "jungle", "mid", "bottom", "utility")


def test_save_load_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "resolve_cache_dir", lambda: str(tmp_path))
    data = config.serialize(
        {
            "top": [],
            "jungle": [(64, "Lee Sin"), (60, "Elise")],
            "mid": [(103, "Ahri")],
            "bottom": [],
            "utility": [],
        },
        [(157, "Yasuo"), (238, "Zed")],
        "flex",
        False,
    )
    config.save_config(data)

    loaded = config.normalize(config.load_config(), _LANES)
    assert loaded["lanes"]["jungle"] == [(64, "Lee Sin"), (60, "Elise")]  # order preserved
    assert loaded["lanes"]["top"] == []
    assert loaded["bans"] == [(157, "Yasuo"), (238, "Zed")]
    assert loaded["mode"] == "flex"
    assert loaded["auto_start"] is False


def test_load_missing_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "resolve_cache_dir", lambda: str(tmp_path / "absent"))
    assert config.load_config() == {}


def test_load_corrupt_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "resolve_cache_dir", lambda: str(tmp_path))
    with open(config.config_path(), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    assert config.load_config() == {}


def test_normalize_defaults_and_skips_junk():
    cfg = config.normalize(
        {
            "lanes": {"mid": [[103, "Ahri"], ["x", "y"], [60]], "unknownlane": [[1, "Z"]]},
            "bans": [[157, "Yasuo"], "junk"],
            "mode": "bogus",
        },
        _LANES,
    )
    assert cfg["lanes"]["mid"] == [(103, "Ahri")]  # malformed pairs skipped
    assert "unknownlane" not in cfg["lanes"]        # unknown lane dropped
    assert cfg["bans"] == [(157, "Yasuo")]
    assert cfg["mode"] == "solo"                    # bogus -> default
    assert cfg["auto_start"] is True                # missing -> default


def test_normalize_empty():
    cfg = config.normalize({}, _LANES)
    assert all(cfg["lanes"][lane] == [] for lane in _LANES)
    assert cfg["bans"] == []
    assert cfg["mode"] == "solo"
    assert cfg["auto_start"] is True


# --- arm/watch validation -------------------------------------------------- #
def _lanes(**kw):
    base = {lane: [] for lane in _LANES}
    base.update(kw)
    return base


_BANS = [(9, "Yasuo"), (10, "Zed")]
_FULL = [(1, "A"), (2, "B")]


def test_validation_passes_complete_config():
    lanes = _lanes(top=_FULL, jungle=[(3, "C"), (4, "D")])
    assert config.validation_error(lanes, _BANS) is None


def test_validation_requires_two_full_lanes():
    err = config.validation_error(_lanes(top=_FULL), _BANS)  # only one full lane
    assert err and "positions" in err.lower()


def test_validation_rejects_partial_lane():
    lanes = _lanes(top=_FULL, jungle=[(3, "C")])  # jungle has one champion
    err = config.validation_error(lanes, _BANS)
    assert err and "jungle" in err


def test_validation_requires_bans():
    lanes = _lanes(top=_FULL, jungle=[(3, "C"), (4, "D")])
    assert config.validation_error(lanes, [(9, "Yasuo")]) is not None  # one ban
    assert config.validation_error(lanes, []) is not None              # no bans
