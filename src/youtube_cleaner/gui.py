from __future__ import annotations

import os
import re
import shlex
import signal
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import Paths
from .gui_commands import CleanupRequest, build_cleanup_args, build_resume_args
from .jobs import JobStore


ANSI_ESCAPE = re.compile(r"(?:\x1b\][^\x07]*(?:\x07|\x1b\\))|(?:\x1b\[[0-?]*[ -/]*[@-~])")


def clean_terminal_output(value: str) -> str:
    value = ANSI_ESCAPE.sub("", value)
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\b", "")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("YouTube Cleaner")
        self.resize(1120, 780)
        self.setMinimumSize(900, 650)

        self.process: QProcess | None = None
        self._running_buttons: list[QPushButton] = []
        self._build_ui()
        self.refresh_jobs()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(12)

        title = QLabel("YouTube Cleaner")
        title_font = QFont()
        title_font.setPointSize(22)
        title_font.setBold(True)
        title.setFont(title_font)
        outer.addWidget(title)

        subtitle = QLabel(
            "Local control panel for the existing safety-checked CLI. "
            "The browser remains visible and every execution is checkpointed."
        )
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        settings = QHBoxLayout()
        settings.addWidget(QLabel("Browser"))
        self.browser = QComboBox()
        self.browser.addItems(("chrome", "chromium"))
        self.browser.setToolTip("Each browser uses its own dedicated profile")
        settings.addWidget(self.browser)
        settings.addSpacing(16)
        settings.addWidget(QLabel("Action delay"))
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.0, 30.0)
        self.delay.setSingleStep(0.25)
        self.delay.setValue(1.5)
        self.delay.setSuffix(" s")
        settings.addWidget(self.delay)
        settings.addSpacing(16)
        self.debug_mode = QCheckBox("Debug diagnostics")
        self.debug_mode.setToolTip("Capture screenshots and HTML when selectors fail")
        settings.addWidget(self.debug_mode)
        settings.addStretch(1)
        self.status_label = QLabel("Ready")
        settings.addWidget(self.status_label)
        outer.addLayout(settings)

        splitter = QSplitter(Qt.Orientation.Vertical)
        tabs = QTabWidget()
        tabs.addTab(self._scrollable(self._actions_tab()), "Library")
        tabs.addTab(self._scrollable(self._playlist_tools_tab()), "Playlist Tools")
        tabs.addTab(self._jobs_tab(), "Jobs")
        tabs.addTab(self._scrollable(self._diagnostics_tab()), "Diagnostics & Profile")
        splitter.addWidget(tabs)
        splitter.addWidget(self._log_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes((450, 280))
        outer.addWidget(splitter, 1)
        self.setCentralWidget(root)

    @staticmethod
    def _scrollable(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _actions_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        quick = QGroupBox("Account and read-only scans")
        quick_layout = QGridLayout(quick)
        account = self._command_button("Verify account", ["account"])
        browser_login = self._command_button("Browser login", ["browser", "login"])
        chrome_login = self._command_button(
            "Native Chrome login",
            ["browser", "login", "--native-chrome"],
            force_browser="chrome",
        )
        quick_layout.addWidget(account, 0, 0)
        quick_layout.addWidget(browser_login, 0, 1)
        quick_layout.addWidget(chrome_login, 0, 2)
        quick_layout.addWidget(self._command_button("Scan Likes", ["scan", "likes"]), 1, 0)
        quick_layout.addWidget(
            self._command_button("Scan Watch Later", ["scan", "watch-later"]), 1, 1
        )
        quick_layout.addWidget(
            self._command_button("Scan Playlists", ["scan", "playlists"]), 1, 2
        )
        quick_layout.addWidget(self._command_button("List Likes", ["likes", "list"]), 2, 0)
        quick_layout.addWidget(
            self._command_button("List Watch Later", ["watch-later", "list"]), 2, 1
        )
        quick_layout.addWidget(
            self._command_button("List Playlists + IDs", ["playlists", "list"]), 2, 2
        )
        layout.addWidget(quick)

        exports = QGroupBox("Exports")
        export_layout = QGridLayout(exports)
        for row, (label, area) in enumerate(
            (("Likes", "likes"), ("Watch Later", "watch-later"), ("Playlists", "playlists"))
        ):
            export_layout.addWidget(QLabel(label), row, 0)
            export_layout.addWidget(
                self._command_button("Export JSON", ["export", area, "--format", "json"]),
                row,
                1,
            )
            export_layout.addWidget(
                self._command_button("Export CSV", ["export", area, "--format", "csv"]),
                row,
                2,
            )
        layout.addWidget(exports)

        cleanup = QGroupBox("Cleanup")
        grid = QGridLayout(cleanup)
        self.area = QComboBox()
        self.area.addItem("Liked videos", "likes")
        self.area.addItem("Watch Later", "watch-later")
        self.area.addItem("Playlists", "playlists")
        self.area.currentIndexChanged.connect(self._update_cleanup_note)
        grid.addWidget(QLabel("Area"), 0, 0)
        grid.addWidget(self.area, 0, 1)

        self.all_items = QCheckBox("All items")
        self.all_items.setChecked(True)
        grid.addWidget(self.all_items, 0, 2)

        self.limit = QSpinBox()
        self.limit.setRange(0, 1_000_000)
        self.limit.setSpecialValueText("No limit")
        grid.addWidget(QLabel("Limit"), 1, 0)
        grid.addWidget(self.limit, 1, 1)

        self.title_filter = QLineEdit()
        self.title_filter.setPlaceholderText("Optional title text")
        grid.addWidget(QLabel("Title contains"), 2, 0)
        grid.addWidget(self.title_filter, 2, 1, 1, 2)

        self.channel_filter = QLineEdit()
        self.channel_filter.setPlaceholderText("Optional channel/owner name")
        grid.addWidget(QLabel("Channel name"), 3, 0)
        grid.addWidget(self.channel_filter, 3, 1, 1, 2)

        self.discover_more = QCheckBox("Keep discovering until three scans find nothing new")
        self.discover_more.setToolTip(
            "Requires All items, no limit, and no filters. Useful for lazy-loaded libraries."
        )
        grid.addWidget(self.discover_more, 4, 0, 1, 3)

        self.typed_confirmation = QCheckBox("Require typing CLEAN before execution")
        grid.addWidget(self.typed_confirmation, 5, 0, 1, 3)

        self.cleanup_note = QLabel()
        self.cleanup_note.setWordWrap(True)
        grid.addWidget(self.cleanup_note, 6, 0, 1, 3)

        buttons = QHBoxLayout()
        self.preview_button = QPushButton("Preview / Dry Run")
        self.execute_button = QPushButton("Execute Cleanup")
        self.execute_button.setProperty("destructive", True)
        self.preview_button.clicked.connect(lambda: self._cleanup(False))
        self.execute_button.clicked.connect(lambda: self._cleanup(True))
        self._running_buttons.extend((self.preview_button, self.execute_button))
        buttons.addStretch(1)
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.execute_button)
        grid.addLayout(buttons, 7, 0, 1, 3)
        layout.addWidget(cleanup)
        layout.addStretch(1)
        self._update_cleanup_note()
        return tab

    def _playlist_tools_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        selection = QGroupBox("Owned playlist selection")
        selection_layout = QGridLayout(selection)
        selection_layout.addWidget(QLabel("Playlist ID"), 0, 0)
        self.playlist_id = QLineEdit()
        self.playlist_id.setPlaceholderText("Copy an ID from List Playlists + IDs")
        selection_layout.addWidget(self.playlist_id, 0, 1, 1, 3)
        selection_layout.addWidget(
            self._command_button("List all playlists", ["playlists", "list"]), 1, 0
        )
        show_button = QPushButton("Show videos")
        show_button.clicked.connect(lambda: self._playlist_read_action("show"))
        duplicates_button = QPushButton("Find duplicates")
        duplicates_button.clicked.connect(lambda: self._playlist_read_action("duplicates"))
        self._running_buttons.extend((show_button, duplicates_button))
        selection_layout.addWidget(show_button, 1, 1)
        selection_layout.addWidget(duplicates_button, 1, 2)
        layout.addWidget(selection)

        clear_group = QGroupBox("Clear video entries from an owned playlist")
        clear_layout = QGridLayout(clear_group)
        self.clear_all = QCheckBox("All entries")
        self.clear_all.setChecked(True)
        clear_layout.addWidget(self.clear_all, 0, 0)
        clear_layout.addWidget(QLabel("Limit"), 0, 1)
        self.clear_limit = QSpinBox()
        self.clear_limit.setRange(0, 1_000_000)
        self.clear_limit.setSpecialValueText("No limit")
        clear_layout.addWidget(self.clear_limit, 0, 2)
        clear_layout.addWidget(QLabel("Title contains"), 1, 0)
        self.clear_title = QLineEdit()
        clear_layout.addWidget(self.clear_title, 1, 1, 1, 2)
        clear_layout.addWidget(QLabel("Channel name"), 2, 0)
        self.clear_channel = QLineEdit()
        clear_layout.addWidget(self.clear_channel, 2, 1, 1, 2)
        clear_preview = QPushButton("Preview Clear")
        clear_execute = QPushButton("Execute Clear")
        clear_preview.clicked.connect(lambda: self._clear_playlist(False))
        clear_execute.clicked.connect(lambda: self._clear_playlist(True))
        self._running_buttons.extend((clear_preview, clear_execute))
        clear_layout.addWidget(clear_preview, 3, 1)
        clear_layout.addWidget(clear_execute, 3, 2)
        layout.addWidget(clear_group)

        dedupe_group = QGroupBox("Deduplicate or delete an owned playlist")
        dedupe_layout = QHBoxLayout(dedupe_group)
        dedupe_layout.addWidget(QLabel("Keep"))
        self.dedupe_keep = QComboBox()
        self.dedupe_keep.addItems(("first", "last"))
        dedupe_layout.addWidget(self.dedupe_keep)
        dedupe_preview = QPushButton("Preview Deduplicate")
        dedupe_execute = QPushButton("Execute Deduplicate")
        delete_preview = QPushButton("Preview Delete Playlist")
        delete_execute = QPushButton("Delete Playlist")
        dedupe_preview.clicked.connect(lambda: self._deduplicate(False))
        dedupe_execute.clicked.connect(lambda: self._deduplicate(True))
        delete_preview.clicked.connect(lambda: self._delete_playlist(False))
        delete_execute.clicked.connect(lambda: self._delete_playlist(True))
        self._running_buttons.extend(
            (dedupe_preview, dedupe_execute, delete_preview, delete_execute)
        )
        dedupe_layout.addWidget(dedupe_preview)
        dedupe_layout.addWidget(dedupe_execute)
        dedupe_layout.addSpacing(18)
        dedupe_layout.addWidget(delete_preview)
        dedupe_layout.addWidget(delete_execute)
        layout.addWidget(dedupe_group)

        empty_group = QGroupBox("Empty owned playlists")
        empty_layout = QHBoxLayout(empty_group)
        empty_layout.addWidget(
            self._command_button("Find Empty Playlists", ["playlists", "empty"])
        )
        empty_preview = QPushButton("Preview Delete Empty")
        empty_execute = QPushButton("Delete Empty Playlists")
        empty_preview.clicked.connect(lambda: self._delete_empty(False))
        empty_execute.clicked.connect(lambda: self._delete_empty(True))
        self._running_buttons.extend((empty_preview, empty_execute))
        empty_layout.addWidget(empty_preview)
        empty_layout.addWidget(empty_execute)
        layout.addWidget(empty_group)

        note = QLabel(
            "These tools only edit playlists owned by the active profile. Saved albums, mixes, "
            "channel playlists, Saved Shorts, and Sounds are not editable item-by-item."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return tab

    def _jobs_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)

        self.jobs_table = QTableWidget(0, 7)
        self.jobs_table.setHorizontalHeaderLabels(
            ("Job", "Operation", "Completed", "Failed", "Skipped", "Remaining", "Updated")
        )
        self.jobs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.jobs_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.jobs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.jobs_table.verticalHeader().setVisible(False)
        header = self.jobs_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(2, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.jobs_table, 1)

        controls = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_jobs)
        controls.addWidget(refresh)
        show_job = QPushButton("Show Details")
        show_job.clicked.connect(self._show_job)
        self._running_buttons.append(show_job)
        controls.addWidget(show_job)
        controls.addSpacing(16)
        controls.addWidget(QLabel("Resume limit"))
        self.resume_limit = QSpinBox()
        self.resume_limit.setRange(0, 1_000_000)
        self.resume_limit.setSpecialValueText("No limit")
        controls.addWidget(self.resume_limit)
        self.resume_discover = QCheckBox("Discover more")
        controls.addWidget(self.resume_discover)
        controls.addStretch(1)
        self.resume_button = QPushButton("Resume Selected Job")
        self.resume_button.clicked.connect(self._resume_job)
        self._running_buttons.append(self.resume_button)
        controls.addWidget(self.resume_button)
        layout.addLayout(controls)

        note = QLabel(
            "Resume rechecks the active YouTube profile before modifying anything. "
            "Failed targets remain resumable."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        return tab

    def _diagnostics_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        diagnostics = QGroupBox("Read-only diagnostics")
        diagnostic_layout = QGridLayout(diagnostics)
        diagnostic_layout.addWidget(
            self._command_button("Authentication Status", ["debug", "auth-status"]), 0, 0
        )
        diagnostic_layout.addWidget(QLabel("Area"), 1, 0)
        self.diagnostic_area = QComboBox()
        self.diagnostic_area.addItem("Liked videos", "likes")
        self.diagnostic_area.addItem("Watch Later", "watch-later")
        self.diagnostic_area.addItem("Playlists", "playlists")
        diagnostic_layout.addWidget(self.diagnostic_area, 1, 1)
        inspect = QPushButton("Inspect Page + Screenshot")
        inspect.clicked.connect(self._inspect_page)
        inspect_menu = QPushButton("Inspect First Video Menu")
        inspect_menu.clicked.connect(self._inspect_menu)
        self._running_buttons.extend((inspect, inspect_menu))
        diagnostic_layout.addWidget(inspect, 1, 2)
        diagnostic_layout.addWidget(inspect_menu, 1, 3)
        diagnostic_layout.addWidget(QLabel("Saved playlist IDs"), 2, 0)
        self.diagnostic_playlist_ids = QLineEdit()
        self.diagnostic_playlist_ids.setPlaceholderText("Optional comma-separated IDs")
        diagnostic_layout.addWidget(self.diagnostic_playlist_ids, 2, 1, 1, 2)
        saved_controls = QPushButton("Inspect Saved Controls")
        saved_controls.clicked.connect(self._inspect_saved_controls)
        self._running_buttons.append(saved_controls)
        diagnostic_layout.addWidget(saved_controls, 2, 3)
        layout.addWidget(diagnostics)

        plans = QGroupBox("Saved plans")
        plan_layout = QHBoxLayout(plans)
        plan_layout.addWidget(QLabel("Plan ID"))
        self.plan_id = QLineEdit()
        self.plan_id.setPlaceholderText("Plan ID created by a preview")
        plan_layout.addWidget(self.plan_id, 1)
        execute_plan = QPushButton("Execute Plan")
        execute_plan.clicked.connect(self._execute_plan)
        self._running_buttons.append(execute_plan)
        plan_layout.addWidget(execute_plan)
        layout.addWidget(plans)

        profile = QGroupBox("Dedicated browser profile")
        profile_layout = QHBoxLayout(profile)
        logout = QPushButton("Log Out / Remove Profile")
        reset = QPushButton("Reset Browser Profile")
        logout.clicked.connect(lambda: self._reset_profile("logout"))
        reset.clicked.connect(lambda: self._reset_profile("reset"))
        self._running_buttons.extend((logout, reset))
        profile_layout.addWidget(logout)
        profile_layout.addWidget(reset)
        profile_layout.addStretch(1)
        layout.addWidget(profile)

        help_text = QLabel(
            "Inspect Page leaves the controlled browser open until you click Continue in the "
            "Library tab. Profile removal deletes only the selected dedicated cleaner profile."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addStretch(1)
        return tab

    def _log_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("Live output"))
        header.addStretch(1)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_log)
        copy = QPushButton("Copy")
        copy.clicked.connect(self._copy_log)
        self.stop_button = QPushButton("Stop Safely")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_process)
        self.continue_button = QPushButton("Continue / Send Enter")
        self.continue_button.setEnabled(False)
        self.continue_button.setToolTip(
            "Continue a login or diagnostic command that is waiting for Enter"
        )
        self.continue_button.clicked.connect(self._continue_process)
        header.addWidget(clear)
        header.addWidget(copy)
        header.addWidget(self.continue_button)
        header.addWidget(self.stop_button)
        layout.addLayout(header)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        fixed = QFont("Menlo")
        fixed.setStyleHint(QFont.StyleHint.Monospace)
        fixed.setPointSize(11)
        self.log.setFont(fixed)
        self.log.setMaximumBlockCount(10_000)
        layout.addWidget(self.log, 1)
        return panel

    def _command_button(
        self,
        label: str,
        args: list[str],
        *,
        force_browser: str | None = None,
    ) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(
            lambda checked=False: self._start_command(args, force_browser=force_browser)
        )
        self._running_buttons.append(button)
        return button

    def _cleanup(self, execute: bool) -> None:
        request = CleanupRequest(
            area=str(self.area.currentData()),
            execute=execute,
            all_items=self.all_items.isChecked(),
            limit=self.limit.value() or None,
            title_contains=self.title_filter.text() or None,
            channel_name=self.channel_filter.text() or None,
            discover_more=self.discover_more.isChecked(),
        )
        try:
            args = build_cleanup_args(request)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid cleanup selection", str(exc))
            return

        if execute:
            area_label = self.area.currentText()
            warning = (
                f"Execute cleanup for {area_label}?\n\n"
                "The active YouTube profile will be checked again before any change. "
                "Every verified change will be checkpointed."
            )
            if request.area == "playlists":
                warning += "\n\nOwned playlists in the selection will be permanently deleted."
            if QMessageBox.question(
                self,
                "Confirm cleanup",
                warning,
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            ) != QMessageBox.StandardButton.Yes:
                return
            if self.typed_confirmation.isChecked() and not self._confirm_clean_phrase():
                return
        self._start_command(args)

    def _required_playlist_id(self) -> str | None:
        playlist_id = self.playlist_id.text().strip()
        if not playlist_id:
            QMessageBox.information(
                self,
                "Playlist ID required",
                "Click List all playlists, then enter the playlist ID you want to use.",
            )
            return None
        return playlist_id

    def _playlist_read_action(self, action: str) -> None:
        playlist_id = self._required_playlist_id()
        if playlist_id:
            self._start_command(["playlists", action, "--playlist", playlist_id])

    def _clear_playlist(self, execute: bool) -> None:
        playlist_id = self._required_playlist_id()
        if not playlist_id:
            return
        title = self.clear_title.text().strip()
        channel = self.clear_channel.text().strip()
        limit = self.clear_limit.value() or None
        if not any((self.clear_all.isChecked(), limit, title, channel)):
            QMessageBox.warning(
                self, "No entries selected", "Select all entries, a limit, or a filter."
            )
            return
        args = ["playlists", "clear", "--playlist", playlist_id]
        if self.clear_all.isChecked():
            args.append("--all")
        if limit:
            args.extend(("--limit", str(limit)))
        if title:
            args.extend(("--title-contains", title))
        if channel:
            args.extend(("--channel-name", channel))
        args.append("--execute" if execute else "--dry-run")
        if execute:
            if not self._confirm_action(
                "Clear playlist entries",
                "Remove the selected video entries from this owned playlist?",
            ):
                return
            args.append("--yes")
        self._start_command(args)

    def _deduplicate(self, execute: bool) -> None:
        playlist_id = self._required_playlist_id()
        if not playlist_id:
            return
        args = [
            "playlists",
            "deduplicate",
            "--playlist",
            playlist_id,
            "--keep",
            self.dedupe_keep.currentText(),
        ]
        if execute:
            if not self._confirm_action(
                "Deduplicate playlist",
                f"Remove duplicate entries while keeping the {self.dedupe_keep.currentText()} occurrence?",
            ):
                return
            args.extend(("--execute", "--yes"))
        self._start_command(args)

    def _delete_playlist(self, execute: bool) -> None:
        playlist_id = self._required_playlist_id()
        if not playlist_id:
            return
        args = ["playlists", "delete", "--playlist", playlist_id]
        if execute:
            if not self._confirm_phrase(
                "DELETE PLAYLIST",
                "Permanently delete owned playlist",
                "This permanently deletes the selected owned playlist.",
            ):
                return
            args.extend(("--execute", "--yes"))
        self._start_command(args)

    def _delete_empty(self, execute: bool) -> None:
        args = ["playlists", "delete-empty"]
        if execute:
            if not self._confirm_phrase(
                "DELETE PLAYLIST",
                "Delete empty playlists",
                "This permanently deletes every playlist that a detailed scan confirms is empty.",
            ):
                return
            args.extend(("--execute", "--yes"))
        self._start_command(args)

    def _confirm_action(self, title: str, text: str) -> bool:
        return QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes

    def _confirm_phrase(self, phrase: str, title: str, text: str) -> bool:
        entered, accepted = QInputDialog.getText(
            self,
            title,
            f"{text}\n\nEnter {phrase} exactly:",
        )
        if accepted and entered != phrase:
            QMessageBox.warning(self, "Action cancelled", f"The text did not match {phrase}.")
        return accepted and entered == phrase

    def _confirm_clean_phrase(self) -> bool:
        return self._confirm_phrase(
            "CLEAN", "Type CLEAN to continue", "The selected cleanup will now execute."
        )

    def _resume_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        operation_item = self.jobs_table.item(self.jobs_table.currentRow(), 1)
        operation = operation_item.text() if operation_item is not None else ""
        if self.resume_discover.isChecked() and operation not in {
            "remove-liked",
            "remove-watch-later",
            "clean-playlists",
        }:
            QMessageBox.warning(
                self,
                "Continuous discovery unavailable",
                "Discover more is supported only by Likes, Watch Later, and playlist-library cleanup jobs.",
            )
            return
        try:
            args = build_resume_args(
                job_id,
                limit=self.resume_limit.value() or None,
                discover_more=self.resume_discover.isChecked(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid resume options", str(exc))
            return
        if QMessageBox.question(
            self,
            "Resume cleanup",
            f"Resume {job_id}?\n\nThe active profile must match the job.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes:
            self._start_command(args)

    def _selected_job_id(self) -> str | None:
        row = self.jobs_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select job", "Select a job first.")
            return None
        item = self.jobs_table.item(row, 0)
        return item.text() if item is not None else None

    def _show_job(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self._start_command(["jobs", "show", job_id])

    def _inspect_page(self) -> None:
        self._start_command(
            ["debug", "inspect", "--area", str(self.diagnostic_area.currentData())]
        )

    def _inspect_menu(self) -> None:
        area = str(self.diagnostic_area.currentData())
        if area == "playlists":
            QMessageBox.information(
                self,
                "Choose a video area",
                "First-video menu inspection supports Liked videos or Watch Later.",
            )
            return
        self._start_command(["debug", "inspect-menu", "--area", area])

    def _inspect_saved_controls(self) -> None:
        args = ["debug", "saved-playlist-controls"]
        playlist_ids = self.diagnostic_playlist_ids.text().strip()
        if playlist_ids:
            args.extend(("--playlist", playlist_ids))
        self._start_command(args)

    def _execute_plan(self) -> None:
        plan_id = self.plan_id.text().strip()
        if not plan_id:
            QMessageBox.information(self, "Plan ID required", "Enter a saved plan ID first.")
            return
        if not self._confirm_phrase(
            "EXECUTE PLAN",
            "Execute saved plan",
            "The plan may permanently modify the profile shown when it was created.",
        ):
            return
        self._start_command(["execute", plan_id, "--yes"])

    def _reset_profile(self, action: str) -> None:
        if not self._confirm_phrase(
            "RESET BROWSER",
            "Remove dedicated browser profile",
            "This deletes only YouTube Cleaner's selected dedicated browser profile and its saved login.",
        ):
            return
        self._start_command(
            ["browser", action],
            initial_input="RESET BROWSER\n",
        )

    def _start_command(
        self,
        args: list[str],
        *,
        force_browser: str | None = None,
        initial_input: str | None = None,
    ) -> None:
        if self.process is not None:
            QMessageBox.information(self, "Command running", "Stop or finish the current command first.")
            return

        browser = force_browser or self.browser.currentText()
        cli_args = [
            "-m",
            "youtube_cleaner",
            "--browser",
            browser,
            "--delay",
            str(self.delay.value()),
        ]
        if self.debug_mode.isChecked():
            cli_args.append("--debug")
        cli_args.extend(args)
        command_text = shlex.join((sys.executable, *cli_args))
        self._append_log(f"\n$ {command_text}\n")

        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(cli_args)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("NO_COLOR", "1")
        environment.insert("TERM", "dumb")
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("COLUMNS", "120")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read_output)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._process_finished)
        if initial_input is not None:
            process.started.connect(lambda: process.write(initial_input.encode("utf-8")))
        self.process = process
        self._set_running(True)
        self.continue_button.setEnabled(
            args[:2] == ["browser", "login"] or args[:2] == ["debug", "inspect"]
        )
        self.status_label.setText("Running")
        process.start()

    def _read_output(self) -> None:
        if self.process is None:
            return
        raw = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._append_log(clean_terminal_output(raw))

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.Crashed and self.status_label.text() == "Stopping safely…":
            return
        process = self.process
        detail = process.errorString() if process is not None else str(error)
        self._append_log(f"\nProcess error: {detail}\n")
        if error == QProcess.ProcessError.FailedToStart:
            self.process = None
            if process is not None:
                process.deleteLater()
            self._set_running(False)
            self.status_label.setText("Failed to start")

    def _process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._read_output()
        self._append_log(f"\nCommand finished with exit code {exit_code}.\n")
        process = self.process
        self.process = None
        if process is not None:
            process.deleteLater()
        self._set_running(False)
        self.status_label.setText("Ready" if exit_code == 0 else f"Exited ({exit_code})")
        self.refresh_jobs()

    def _set_running(self, running: bool) -> None:
        for button in self._running_buttons:
            button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        if not running:
            self.continue_button.setEnabled(False)

    def _stop_process(self) -> None:
        if self.process is None:
            return
        pid = int(self.process.processId())
        if pid <= 0:
            return
        self.status_label.setText("Stopping safely…")
        self.stop_button.setEnabled(False)
        self._append_log("\nSending a safe interrupt; waiting for the current checkpoint…\n")
        if os.name == "posix":
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        else:
            self.process.terminate()

    def _continue_process(self) -> None:
        if self.process is not None:
            self.process.write(b"\n")
            self._append_log("\n[Continue sent]\n")

    def _append_log(self, value: str) -> None:
        if not value:
            return
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(value)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _clear_log(self) -> None:
        self.log.clear()

    def _copy_log(self) -> None:
        QApplication.clipboard().setText(self.log.toPlainText())

    def _update_cleanup_note(self) -> None:
        if not hasattr(self, "cleanup_note"):
            return
        if self.area.currentData() == "playlists":
            self.cleanup_note.setText(
                "Playlist cleanup deletes owned playlists and removes saved external playlists. "
                "YouTube-managed Saved Shorts and Sounds collections are excluded because YouTube "
                "does not provide a delete or remove control for them."
            )
        else:
            self.cleanup_note.setText(
                "Start with Preview or a limit of 1. Continuous discovery is available only for an unrestricted All-items run."
            )

    def refresh_jobs(self) -> None:
        try:
            jobs = JobStore(Paths.default().jobs).list()
        except Exception as exc:
            self.status_label.setText(f"Could not load jobs: {exc}")
            return
        self.jobs_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            values = (
                job.job_id,
                job.operation,
                str(len(job.completed)),
                str(len(job.failed)),
                str(len(job.skipped)),
                str(len(job.remaining)),
                job.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {2, 3, 4, 5}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.jobs_table.setItem(row, column, item)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        if self.process is None:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "Command still running",
            "Send a safe interrupt and close after the command stops?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self._stop_process()
        event.ignore()
        process = self.process
        if process is not None:
            process.finished.connect(lambda *_: QTimer.singleShot(0, self.close))


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("YouTube Cleaner")
    app.setOrganizationName("youtube-cleaner")
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
