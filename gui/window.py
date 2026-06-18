"""The main GUI window.

Thin Qt layer: it owns widgets and connects them to the headless src/ logic and
the gui.viewmodel transforms. The champion search *pre-configures* the draft —
the user assigns champions per lane and builds a ban list, then Arm hands that
to make_autopilot (state.AUTOPILOT) and the existing run_draft auto-bans/picks
when it's their turn. Nothing here picks or bans immediately. All rendering
decisions live in viewmodel.py; all client actions live in src/. This file only
moves data between the two, on the Qt main thread.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src import autopilot as autopilot_mod
from src import manual
from src.constants import LANE_ALIASES
from src.events import (
    ChampSelectEndedUpdate,
    ChampSelectUpdate,
    ConnectedUpdate,
    DisconnectedUpdate,
    NoticeUpdate,
    PhaseUpdate,
)
from gui import config, viewmodel
from gui.bridge import LcuThread, QtEventSink, arm_autopilot

# Lanes offered in the config UI, in role order. The value is the alias passed
# to make_autopilot (LANE_ALIASES canonicalises it). Two picks per lane mirror
# the CLI's `--lane POSITION CHAMP1 CHAMP2`.
_LANES = ("top", "jungle", "mid", "bottom", "utility")
_MAX_PER_LANE = 2


class MainWindow(QMainWindow):
    # Emitted from any thread to push a (text, level) status line; the connected
    # slot runs queued on the main thread, so cross-thread callbacks can report
    # results without touching widgets directly.
    _status_signal = Signal(str, str)

    def __init__(self, lcu: LcuThread, sink: QtEventSink) -> None:
        super().__init__()
        self._lcu = lcu
        self.setWindowTitle("LCU Automator")
        self.resize(560, 820)

        # Pre-configured draft intentions, populated from the search box and
        # handed to make_autopilot on Arm. (id, name) so we can render + dedupe.
        self._lane_choices: dict[str, list[tuple[int, str]]] = {lane: [] for lane in _LANES}
        self._ban_choices: list[tuple[int, str]] = []

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._build_user_box())
        layout.addWidget(self._build_autopilot_box())
        layout.addWidget(self._build_search_box())
        layout.addWidget(self._build_config_box())
        layout.addWidget(self._build_champ_select_box(), stretch=1)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self.setCentralWidget(root)

        # Updates arrive (queued) on this, the main thread.
        sink.updated.connect(self._on_update)
        self._status_signal.connect(self._set_status)

        # Restore last session's draft config, then start persisting changes.
        # (Connect the persist triggers AFTER loading so applying the saved mode
        # / checkbox doesn't immediately re-save.)
        self._load_config()
        self._mode.currentTextChanged.connect(lambda _: self._persist_config())
        self._auto_start.toggled.connect(lambda _: self._persist_config())

    # --- widget construction ----------------------------------------------- #
    def _build_user_box(self) -> QWidget:
        box = QGroupBox("Logged-in user")
        lay = QVBoxLayout(box)
        self._user_label = QLabel("Not connected")
        self._phase_label = QLabel(viewmodel.phase_text(None))  # always-visible phase
        lay.addWidget(self._user_label)
        lay.addWidget(self._phase_label)
        return box

    def _build_autopilot_box(self) -> QWidget:
        box = QGroupBox("Party")
        lay = QHBoxLayout(box)
        # Mode is the queue auto-start uses to create the lobby; watch-only
        # ignores it for starting but it's still recorded on the autopilot.
        self._mode = QComboBox()
        self._mode.addItems(["solo", "flex", "aram"])
        # Checked = auto-start (create lobby + queue); unchecked = watch only,
        # still auto pick/ban. Mirrors the CLI's default vs --no-start.
        self._auto_start = QCheckBox("Auto-start party (else watch + auto pick/ban)")
        self._auto_start.setChecked(True)
        arm = QPushButton("Arm")
        arm.clicked.connect(self._on_arm)
        disarm = QPushButton("Disarm")
        disarm.clicked.connect(lambda: self._arm(None, "Autopilot disarmed."))
        lay.addWidget(QLabel("Mode:"))
        lay.addWidget(self._mode)
        lay.addWidget(self._auto_start, stretch=1)
        lay.addWidget(arm)
        lay.addWidget(disarm)
        return box

    def _build_search_box(self) -> QWidget:
        box = QGroupBox("Champion search (pre-configure pick / ban)")
        lay = QVBoxLayout(box)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Type a champion name to add to a lane or the ban list...")
        self._search.textChanged.connect(self._on_search)
        self._results = QListWidget()
        self._results.setMaximumHeight(140)

        controls = QHBoxLayout()
        self._lane_select = QComboBox()
        self._lane_select.addItems(_LANES)
        add_to_lane = QPushButton("Add to lane")
        add_to_lane.clicked.connect(self._on_add_to_lane)
        add_to_bans = QPushButton("Add to bans")
        add_to_bans.clicked.connect(self._on_add_to_bans)
        controls.addWidget(QLabel("Lane:"))
        controls.addWidget(self._lane_select)
        controls.addWidget(add_to_lane)
        controls.addWidget(add_to_bans)

        lay.addWidget(self._search)
        lay.addWidget(self._results)
        lay.addLayout(controls)
        self._on_search("")  # seed the browse list
        return box

    def _build_config_box(self) -> QWidget:
        box = QGroupBox("Draft config (armed on 'Arm')")
        lay = QVBoxLayout(box)
        self._config_lanes = QLabel()
        self._config_lanes.setWordWrap(True)
        self._config_bans = QLabel()
        self._config_bans.setWordWrap(True)
        clear = QPushButton("Clear config")
        clear.clicked.connect(self._on_clear_config)
        lay.addWidget(self._config_lanes)
        lay.addWidget(self._config_bans)
        lay.addWidget(clear)
        self._refresh_config_labels()
        return box

    def _build_champ_select_box(self) -> QWidget:
        box = QGroupBox("Champ select")
        lay = QVBoxLayout(box)
        self._cs_summary = QLabel("(not in champ select)")
        self._cs_team = QListWidget()
        self._cs_enemy = QLabel("Enemy: -")
        self._cs_bans = QLabel("Bans: -")
        self._cs_enemy.setWordWrap(True)
        self._cs_bans.setWordWrap(True)
        lay.addWidget(self._cs_summary)
        lay.addWidget(QLabel("Your team:"))
        lay.addWidget(self._cs_team, stretch=1)
        lay.addWidget(self._cs_enemy)
        lay.addWidget(self._cs_bans)
        return box

    # --- event sink updates (main thread) ---------------------------------- #
    def _on_update(self, update) -> None:
        if isinstance(update, ConnectedUpdate):
            self._user_label.setText(viewmodel.summoner_text(update.summoner))
        elif isinstance(update, DisconnectedUpdate):
            self._user_label.setText("Client closed")
            self._phase_label.setText(viewmodel.phase_text(None))
        elif isinstance(update, PhaseUpdate):
            self._phase_label.setText(viewmodel.phase_text(update.phase))
        elif isinstance(update, ChampSelectUpdate):
            self._render_champ_select(update.view)
        elif isinstance(update, ChampSelectEndedUpdate):
            self._clear_champ_select()
        elif isinstance(update, NoticeUpdate):
            self._set_status(update.text, update.level)

    def _render_champ_select(self, view) -> None:
        lines = viewmodel.champ_select_lines(view)
        self._cs_summary.setText(lines["summary"][0])
        self._cs_team.clear()
        self._cs_team.addItems(lines["team"])
        self._cs_enemy.setText("Enemy: " + (", ".join(lines["enemy"]) or "-"))
        my_bans = ", ".join(lines["my_bans"]) or "-"
        their_bans = ", ".join(lines["their_bans"]) or "-"
        self._cs_bans.setText(f"Bans - yours: {my_bans} | theirs: {their_bans}")

    def _clear_champ_select(self) -> None:
        self._cs_summary.setText("(not in champ select)")
        self._cs_team.clear()
        self._cs_enemy.setText("Enemy: -")
        self._cs_bans.setText("Bans: -")

    # --- search + config (main thread) ------------------------------------- #
    def _on_search(self, text: str) -> None:
        self._results.clear()
        for champ_id, name in manual.search_champions(text):
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, champ_id)  # carry the id alongside the name
            self._results.addItem(item)

    def _selected_champion(self) -> tuple[int, str] | None:
        item = self._results.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole), item.text()

    def _on_add_to_lane(self) -> None:
        champ = self._selected_champion()
        if champ is None:
            self._set_status("Pick a champion in the search list first.", "warn")
            return
        lane = self._lane_select.currentText()
        choices = self._lane_choices[lane]
        if champ[0] in {cid for cid, _ in choices}:
            self._set_status(f"{champ[1]} is already set for {lane}.", "warn")
            return
        if len(choices) >= _MAX_PER_LANE:
            self._set_status(f"{lane} already has {_MAX_PER_LANE} champions.", "warn")
            return
        choices.append(champ)
        self._refresh_config_labels()
        self._persist_config()
        self._set_status(f"Added {champ[1]} to {lane}.", "info")

    def _on_add_to_bans(self) -> None:
        champ = self._selected_champion()
        if champ is None:
            self._set_status("Pick a champion in the search list first.", "warn")
            return
        if champ[0] in {cid for cid, _ in self._ban_choices}:
            self._set_status(f"{champ[1]} is already in the ban list.", "warn")
            return
        self._ban_choices.append(champ)
        self._refresh_config_labels()
        self._persist_config()
        self._set_status(f"Added {champ[1]} to bans.", "info")

    def _on_clear_config(self) -> None:
        for lane in self._lane_choices:
            self._lane_choices[lane] = []
        self._ban_choices = []
        self._refresh_config_labels()
        self._persist_config()
        self._set_status("Cleared draft config.", "info")

    # --- persistence (last config across sessions) ------------------------- #
    def _load_config(self) -> None:
        cfg = config.normalize(config.load_config(), _LANES)
        self._lane_choices = {lane: list(choices) for lane, choices in cfg["lanes"].items()}
        self._ban_choices = list(cfg["bans"])
        self._mode.setCurrentText(cfg["mode"])
        self._auto_start.setChecked(cfg["auto_start"])
        self._refresh_config_labels()

    def _persist_config(self) -> None:
        config.save_config(
            config.serialize(
                self._lane_choices,
                self._ban_choices,
                self._mode.currentText(),
                self._auto_start.isChecked(),
            )
        )

    def _refresh_config_labels(self) -> None:
        configured = [
            f"{lane}: {', '.join(name for _, name in choices)}"
            for lane, choices in self._lane_choices.items()
            if choices
        ]
        self._config_lanes.setText("Lanes - " + ("  |  ".join(configured) or "(none)"))
        bans = ", ".join(name for _, name in self._ban_choices) or "(none)"
        self._config_bans.setText("Bans (in order) - " + bans)

    # --- arming ------------------------------------------------------------ #
    def _on_arm(self) -> None:
        # Refuse to arm OR watch with an incomplete config -- the autopilot needs
        # two full positions (so the 2nd role pref is a real lane, not FILL) and
        # bans before it can draft for you.
        error = config.validation_error(self._lane_choices, self._ban_choices)
        if error:
            self._set_status(error, "error")
            return
        try:
            ap = autopilot_mod.make_autopilot(
                self._mode.currentText(),
                lanes=viewmodel.autopilot_lanes(self._lane_choices),
                bans=[name for _, name in self._ban_choices],
                start=self._auto_start.isChecked(),
                resolve=manual.resolve_champion,
            )
        except Exception as exc:  # bad config -> surface, don't arm
            self._set_status(f"Could not arm: {exc}", "error")
            return
        kind = "auto-start" if ap.start else "watch + auto pick/ban"
        self._arm(ap, f"Autopilot armed: {ap.mode} ({kind}).")

    def _arm(self, autopilot, message: str) -> None:
        arm_autopilot(self._lcu, autopilot)
        self._set_status(message, "info")

    def _set_status(self, text: str, level: str) -> None:
        colors = {"info": "black", "warn": "darkorange", "error": "red"}
        self._status.setStyleSheet(f"color: {colors.get(level, 'black')};")
        self._status.setText(text)


# LANE_ALIASES is imported to keep the UI's lane list honest: every entry in
# _LANES must be a known alias make_autopilot will accept.
assert all(lane in LANE_ALIASES for lane in _LANES)
