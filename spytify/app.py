from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSlider,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
)

from . import __app_name__
from .importer import SUPPORTED_AUDIO_EXTENSIONS
from .models import DEFAULT_PLAYLIST_ID, Playlist, Song
from .storage import LibraryStore


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, remainder = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


class LibraryPickerDialog(QDialog):
    def __init__(self, songs: list[Song], excluded_ids: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add songs from library")
        self.resize(520, 560)
        self.songs = songs
        self.excluded_ids = excluded_ids
        self.selected_song_ids: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search imported songs")
        self.search_input.textChanged.connect(self.populate)
        layout.addWidget(self.search_input)

        self.song_list = QListWidget()
        self.song_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        layout.addWidget(self.song_list, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        add_button = QPushButton("Add Selected")
        add_button.setObjectName("PrimaryButton")
        add_button.clicked.connect(self.accept)
        button_row.addWidget(cancel_button)
        button_row.addWidget(add_button)
        layout.addLayout(button_row)

        self.populate()

    def populate(self) -> None:
        self.song_list.clear()
        query = self.search_input.text().strip().lower()
        for song in self.songs:
            if song.id in self.excluded_ids:
                continue
            haystack = f"{song.title} {song.artist} {song.album}".lower()
            if query and query not in haystack:
                continue
            item = QListWidgetItem(f"{song.title} - {song.artist}")
            item.setData(Qt.ItemDataRole.UserRole, song.id)
            self.song_list.addItem(item)

    def accept(self) -> None:
        self.selected_song_ids = [
            str(item.data(Qt.ItemDataRole.UserRole)) for item in self.song_list.selectedItems()
        ]
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = LibraryStore()
        self.current_playlist_id = DEFAULT_PLAYLIST_ID
        self.displayed_song_ids: list[str] = []
        self.current_song_index = -1
        
        # Queue system
        self.queue: list[str] = []
        self.queue_index: int = -1
        
        # Previous button timing
        self.last_prev_press_time: float = 0
        self.PREV_THRESHOLD_SECONDS: float = 3.0  # Go to previous if pressed within 3 seconds
        
        # Fade settings (in seconds)
        self.fade_in_duration: float = 0.0
        self.fade_out_duration: float = 0.0
        self.fade_timer: QTimer | None = None
        self._current_volume: float = 0.8
        
        # System tray
        self.tray_icon: QSystemTrayIcon | None = None
        
        # Player setup
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player.setAudioOutput(self.audio_output)

        self.setWindowTitle(__app_name__)
        self.resize(1180, 760)
        self.setMinimumSize(920, 620)
        
        # Load settings
        self.load_settings()

        self.build_ui()
        self.build_tray()
        self.bind_player()
        self.install_shortcuts()
        self.refresh_all()
        
        # Auto-save temp files on exit
        self.destroyed.connect(self.store.clear_temp_files)

    def load_settings(self) -> None:
        """Load user settings from store."""
        settings_path = self.store.root / "settings.json"
        if settings_path.exists():
            import json
            try:
                settings = json.loads(settings_path.read_text())
                self.fade_in_duration = float(settings.get("fade_in", 0.0))
                self.fade_out_duration = float(settings.get("fade_out", 0.0))
            except (json.JSONDecodeError, ValueError):
                pass

    def save_settings(self) -> None:
        """Save user settings to store."""
        settings_path = self.store.root / "settings.json"
        import json
        settings_path.write_text(json.dumps({
            "fade_in": self.fade_in_duration,
            "fade_out": self.fade_out_duration,
        }), encoding="utf-8")

    def build_tray(self) -> None:
        """Build system tray icon."""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip(__app_name__)
        self.tray_icon.activated.connect(self.on_tray_activated)
        
        # Create tray menu
        tray_menu = QMenu(self)
        show_action = tray_menu.addAction("Show")
        show_action.triggered.connect(self.show)
        tray_menu.addSeparator()
        
        play_pause_action = tray_menu.addAction("Play/Pause")
        play_pause_action.triggered.connect(self.toggle_playback)
        
        prev_action = tray_menu.addAction("Previous")
        prev_action.triggered.connect(self.play_previous)
        
        next_action = tray_menu.addAction("Next")
        next_action.triggered.connect(self.play_next)
        
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_app)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_MediaPlay))
        self.tray_icon.show()

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()
            self.activateWindow()

    def quit_app(self) -> None:
        """Quit the application completely."""
        self.store.clear_temp_files()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        """Override close event to minimize to tray instead of closing."""
        event.ignore()
        self.hide()
        if self.tray_icon:
            self.tray_icon.showMessage(__app_name__, "Spytify is still playing in the background", QSystemTrayIcon.MessageIcon.Information, 2000)

    def build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(284)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 18, 18, 18)
        sidebar_layout.setSpacing(12)

        title = QLabel("Spytify")
        title.setObjectName("AppTitle")
        sidebar_layout.addWidget(title)

        self.import_button = QPushButton("Import Music")
        self.import_button.setObjectName("PrimaryButton")
        self.import_button.clicked.connect(self.import_music)
        sidebar_layout.addWidget(self.import_button)

        self.new_playlist_button = QPushButton("New Playlist")
        self.new_playlist_button.clicked.connect(self.create_playlist)
        sidebar_layout.addWidget(self.new_playlist_button)
        
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.show_settings)
        sidebar_layout.addWidget(self.settings_button)

        section_label = QLabel("Playlists")
        section_label.setObjectName("SectionLabel")
        sidebar_layout.addWidget(section_label)

        self.playlist_scroll = QScrollArea()
        self.playlist_scroll.setWidgetResizable(True)
        self.playlist_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.playlist_container = QWidget()
        self.playlist_layout = QVBoxLayout(self.playlist_container)
        self.playlist_layout.setContentsMargins(0, 0, 0, 0)
        self.playlist_layout.setSpacing(8)
        self.playlist_scroll.setWidget(self.playlist_container)
        sidebar_layout.addWidget(self.playlist_scroll, 1)
        
        # Queue section
        queue_label = QLabel("Queue")
        queue_label.setObjectName("SectionLabel")
        sidebar_layout.addWidget(queue_label)
        
        self.queue_list = QListWidget()
        self.queue_list.setObjectName("QueueList")
        self.queue_list.setMinimumHeight(120)
        self.queue_list.setMaximumHeight(180)
        self.queue_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.queue_list.itemDoubleClicked.connect(self.play_from_queue)
        sidebar_layout.addWidget(self.queue_list)

        data_hint = QLabel(f"Library: {self.store.root}")
        data_hint.setObjectName("DataHint")
        data_hint.setWordWrap(True)
        sidebar_layout.addWidget(data_hint)

        root.addWidget(sidebar)

        main_panel = QWidget()
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(24, 22, 24, 18)
        main_layout.setSpacing(16)
        root.addWidget(main_panel, 1)

        header_layout = QGridLayout()
        header_layout.setColumnStretch(0, 1)
        header_layout.setColumnStretch(1, 0)
        main_layout.addLayout(header_layout)

        self.playlist_name_input = QLineEdit()
        self.playlist_name_input.setObjectName("PlaylistTitle")
        self.playlist_name_input.editingFinished.connect(self.rename_current_playlist)
        header_layout.addWidget(self.playlist_name_input, 0, 0)

        self.playlist_meta_label = QLabel()
        self.playlist_meta_label.setObjectName("MutedLabel")
        header_layout.addWidget(self.playlist_meta_label, 1, 0)

        action_row = QHBoxLayout()
        self.add_existing_button = QPushButton("Add From Library")
        self.add_existing_button.clicked.connect(self.add_existing_songs)
        self.remove_button = QPushButton("Remove From Playlist")
        self.remove_button.clicked.connect(self.remove_selected_songs)
        action_row.addWidget(self.add_existing_button)
        action_row.addWidget(self.remove_button)
        header_layout.addLayout(action_row, 0, 1, 2, 1)

        self.song_table = QTableWidget(0, 4)
        self.song_table.setHorizontalHeaderLabels(["Title", "Artist", "Album", "Time"])
        self.song_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.song_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.song_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.song_table.verticalHeader().setVisible(False)
        self.song_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.song_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.song_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.song_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.song_table.doubleClicked.connect(self.play_selected_song)
        self.song_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.song_table.customContextMenuRequested.connect(self.show_song_context_menu)
        main_layout.addWidget(self.song_table, 1)

        player_bar = QFrame()
        player_bar.setObjectName("PlayerBar")
        player_layout = QGridLayout(player_bar)
        player_layout.setContentsMargins(14, 12, 14, 12)
        player_layout.setHorizontalSpacing(12)
        player_layout.setVerticalSpacing(8)
        main_layout.addWidget(player_bar)

        self.now_playing_label = QLabel("Nothing playing")
        self.now_playing_label.setObjectName("NowPlaying")
        self.now_playing_label.setMinimumWidth(260)
        player_layout.addWidget(self.now_playing_label, 0, 0, 2, 1)

        self.previous_button = QPushButton("Prev")
        self.previous_button.clicked.connect(self.play_previous)
        self.play_button = QPushButton("Play")
        self.play_button.setObjectName("PrimaryButton")
        self.play_button.clicked.connect(self.toggle_playback)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self.play_next)
        controls = QHBoxLayout()
        controls.addWidget(self.previous_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.next_button)
        player_layout.addLayout(controls, 0, 1)

        self.position_label = QLabel("0:00")
        self.duration_label = QLabel("0:00")
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(self.player.setPosition)
        player_layout.addWidget(self.position_label, 1, 1)
        player_layout.addWidget(self.seek_slider, 1, 2)
        player_layout.addWidget(self.duration_label, 1, 3)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100))
        player_layout.addWidget(QLabel("Volume"), 0, 3)
        player_layout.addWidget(self.volume_slider, 0, 4)

        self.setStyleSheet(STYLESHEET)

    def bind_player(self) -> None:
        self.player.playbackStateChanged.connect(self.on_playback_state_changed)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        self.player.errorOccurred.connect(self.on_player_error)

    def install_shortcuts(self) -> None:
        import_action = QAction(self)
        import_action.setShortcut(QKeySequence.StandardKey.Open)
        import_action.triggered.connect(self.import_music)
        self.addAction(import_action)

        play_action = QAction(self)
        play_action.setShortcut(QKeySequence(Qt.Key.Key_Space))
        play_action.triggered.connect(self.toggle_playback)
        self.addAction(play_action)
        
        # Numpad keybindings
        pause_action = QAction(self)
        pause_action.setShortcut(QKeySequence(Qt.Key.Key_NumLock, Qt.Key.Key_5))
        pause_action.setEnabled(False)  # Disabled to avoid conflict with numlock
        # Use a workaround for numpad - check in keyPressEvent
        
        prev_action = QAction(self)
        prev_action.setShortcut(QKeySequence(Qt.Key.Key_Left))
        prev_action.triggered.connect(self.play_previous)
        self.addAction(prev_action)
        
        next_action = QAction(self)
        next_action.setShortcut(QKeySequence(Qt.Key.Key_Right))
        next_action.triggered.connect(self.play_next)
        self.addAction(next_action)

    def keyPressEvent(self, event) -> None:
        """Handle keyboard shortcuts including numpad."""
        from PySide6.QtGui import QKeyEvent
        
        key = event.key()
        modifiers = event.modifiers()
        
        # Numpad 5 - Play/Pause
        if key == Qt.Key.Key_5 and modifiers == Qt.KeyboardModifier.KeypadModifier:
            self.toggle_playback()
            return
        
        # Numpad 4 - Previous
        if key == Qt.Key.Key_4 and modifiers == Qt.KeyboardModifier.KeypadModifier:
            self.play_previous()
            return
        
        # Numpad 6 - Next
        if key == Qt.Key.Key_6 and modifiers == Qt.KeyboardModifier.KeypadModifier:
            self.play_next()
            return
        
        # Delete key - Remove selected songs from playlist
        if key == Qt.Key.Key_Delete:
            self.remove_selected_songs()
            return
        
        super().keyPressEvent(event)

    def refresh_all(self) -> None:
        self.refresh_playlist_tiles()
        self.refresh_playlist_view()

    def refresh_playlist_tiles(self) -> None:
        while self.playlist_layout.count():
            item = self.playlist_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        all_count = len(self.store.songs)
        self.add_playlist_tile(DEFAULT_PLAYLIST_ID, "All Imported", all_count)
        for playlist in self.store.sorted_playlists():
            self.add_playlist_tile(playlist.id, playlist.name, len(playlist.song_ids), playlist)
        self.playlist_layout.addStretch(1)

    def add_playlist_tile(
        self,
        playlist_id: str,
        name: str,
        count: int,
        playlist: Playlist | None = None,
    ) -> None:
        button = QPushButton(f"{name}\n{count} songs")
        button.setProperty("playlistTile", True)
        button.setProperty("selected", playlist_id == self.current_playlist_id)
        button.clicked.connect(lambda checked=False, pid=playlist_id: self.select_playlist(pid))
        if playlist is not None:
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, pid=playlist_id, btn=button: self.show_playlist_menu(pid, btn, pos)
            )
        self.playlist_layout.addWidget(button)

    def select_playlist(self, playlist_id: str) -> None:
        self.current_playlist_id = playlist_id
        self.refresh_all()

    def show_playlist_menu(self, playlist_id: str, source: QWidget, pos) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        action = menu.exec(source.mapToGlobal(pos))
        if action == rename_action:
            self.rename_playlist_dialog(playlist_id)
        elif action == delete_action:
            self.delete_playlist(playlist_id)

    def refresh_playlist_view(self) -> None:
        is_default = self.current_playlist_id == DEFAULT_PLAYLIST_ID
        playlist = None if is_default else self.store.playlists.get(self.current_playlist_id)
        if not is_default and playlist is None:
            self.current_playlist_id = DEFAULT_PLAYLIST_ID
            is_default = True

        if is_default:
            name = "All Imported"
            song_ids = [song.id for song in self.store.sorted_songs()]
        else:
            assert playlist is not None
            name = playlist.name
            song_ids = [song_id for song_id in playlist.song_ids if song_id in self.store.songs]

        self.displayed_song_ids = song_ids
        self.playlist_name_input.blockSignals(True)
        self.playlist_name_input.setText(name)
        self.playlist_name_input.setReadOnly(is_default)
        self.playlist_name_input.blockSignals(False)
        self.add_existing_button.setEnabled(not is_default and bool(self.store.songs))
        self.remove_button.setEnabled(not is_default)

        self.playlist_meta_label.setText(f"{len(song_ids)} songs")
        self.populate_song_table(song_ids)

    def populate_song_table(self, song_ids: list[str]) -> None:
        self.song_table.setRowCount(0)
        for row, song_id in enumerate(song_ids):
            song = self.store.songs.get(song_id)
            if song is None:
                continue
            self.song_table.insertRow(row)
            values = [song.title, song.artist, song.album, format_duration(song.duration_seconds)]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, song.id)
                self.song_table.setItem(row, column, item)

    def current_selected_song_ids(self) -> list[str]:
        selected_rows = sorted({index.row() for index in self.song_table.selectedIndexes()})
        song_ids: list[str] = []
        for row in selected_rows:
            item = self.song_table.item(row, 0)
            if item is not None:
                song_ids.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return song_ids

    def create_playlist(self) -> None:
        name, accepted = QInputDialog.getText(self, "New playlist", "Playlist name:")
        if not accepted:
            return
        playlist = self.store.create_playlist(name)
        self.current_playlist_id = playlist.id
        self.refresh_all()

    def rename_current_playlist(self) -> None:
        if self.current_playlist_id == DEFAULT_PLAYLIST_ID:
            return
        text = self.playlist_name_input.text()
        self.store.rename_playlist(self.current_playlist_id, text)
        self.refresh_all()

    def rename_playlist_dialog(self, playlist_id: str) -> None:
        playlist = self.store.playlists.get(playlist_id)
        if playlist is None:
            return
        name, accepted = QInputDialog.getText(self, "Rename playlist", "Playlist name:", text=playlist.name)
        if accepted:
            self.store.rename_playlist(playlist_id, name)
            self.refresh_all()

    def delete_playlist(self, playlist_id: str) -> None:
        playlist = self.store.playlists.get(playlist_id)
        if playlist is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete playlist",
            f"Delete '{playlist.name}'? Imported music files stay in your library.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_playlist(playlist_id)
        if self.current_playlist_id == playlist_id:
            self.current_playlist_id = DEFAULT_PLAYLIST_ID
        self.refresh_all()

    def import_music(self) -> None:
        extensions = " ".join(f"*{extension}" for extension in sorted(SUPPORTED_AUDIO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Import music",
            str(Path.home()),
            f"Audio Files ({extensions});;All Files (*)",
        )
        if not files:
            return

        progress = QProgressDialog("Importing and converting music...", "Cancel", 0, len(files), self)
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)

        imported_ids: list[str] = []
        skipped = 0
        errors: list[str] = []

        for index, file_name in enumerate(files, start=1):
            progress.setValue(index - 1)
            progress.setLabelText(f"Importing {Path(file_name).name}")
            QApplication.processEvents()
            if progress.wasCanceled():
                break
            try:
                song, created = self.store.import_music_file(Path(file_name))
                imported_ids.append(song.id)
                if not created:
                    skipped += 1
            except Exception as exc:
                errors.append(f"{Path(file_name).name}: {exc}")

        progress.setValue(len(files))

        if self.current_playlist_id != DEFAULT_PLAYLIST_ID and imported_ids:
            self.store.add_songs_to_playlist(self.current_playlist_id, imported_ids)

        self.refresh_all()
        self.show_import_result(imported_ids, skipped, errors)

    def show_import_result(self, imported_ids: list[str], skipped: int, errors: list[str]) -> None:
        created = max(0, len(imported_ids) - skipped)
        lines = [f"Imported {created} new song(s)."]
        if skipped:
            lines.append(f"Skipped {skipped} duplicate song(s).")
        if errors:
            lines.append("")
            lines.append("Some files could not be imported:")
            lines.extend(errors[:6])
            if len(errors) > 6:
                lines.append(f"...and {len(errors) - 6} more.")
        QMessageBox.information(self, "Import complete", "\n".join(lines))

    def add_existing_songs(self) -> None:
        if self.current_playlist_id == DEFAULT_PLAYLIST_ID:
            return
        playlist = self.store.playlists.get(self.current_playlist_id)
        if playlist is None:
            return

        dialog = LibraryPickerDialog(self.store.sorted_songs(), set(playlist.song_ids), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.selected_song_ids:
            self.store.add_songs_to_playlist(self.current_playlist_id, dialog.selected_song_ids)
            self.refresh_all()

    def remove_selected_songs(self) -> None:
        if self.current_playlist_id == DEFAULT_PLAYLIST_ID:
            return
        song_ids = self.current_selected_song_ids()
        if not song_ids:
            return
        self.store.remove_songs_from_playlist(self.current_playlist_id, song_ids)
        self.refresh_all()

    def play_selected_song(self, _index=None) -> None:
        selected_ids = self.current_selected_song_ids()
        if selected_ids:
            self.play_song(selected_ids[0])

    def play_song(self, song_id: str) -> None:
        song = self.store.songs.get(song_id)
        if song is None:
            return
        
        # Try to get song from playlist's embedded files first, then fall back to library
        song_path = self.store.get_playlist_song_path(self.current_playlist_id, song_id)
        if song_path is None:
            song_path = self.store.song_path(song)
        
        if not song_path.exists():
            QMessageBox.warning(self, "Missing file", f"Could not find {song_path}")
            return
        
        # Stop any current fade
        if self.fade_timer:
            self.fade_timer.stop()
        
        self.current_song_index = self.displayed_song_ids.index(song_id) if song_id in self.displayed_song_ids else -1
        self.player.setSource(QUrl.fromLocalFile(str(song_path)))
        
        # Apply fade-in if set
        if self.fade_in_duration > 0:
            self._start_fade_in()
        else:
            self.player.play()
        
        self.now_playing_label.setText(f"{song.title}\n{song.artist}")
        
        # Update tray tooltip
        if self.tray_icon:
            self.tray_icon.setToolTip(f"{song.title} - {song.artist}")

    def _start_fade_in(self) -> None:
        """Start fade-in effect."""
        if self.fade_timer:
            self.fade_timer.stop()
        
        self.audio_output.setVolume(0)
        self.player.play()
        
        steps = 20  # Number of volume steps
        interval = int(self.fade_in_duration * 1000 / steps)  # Interval in ms
        self._fade_step = 0
        self._fade_steps = steps
        self._target_volume = self._current_volume
        
        self.fade_timer = QTimer(self)
        self.fade_timer.timeout.connect(self._fade_in_step)
        self.fade_timer.start(max(interval, 50))  # At least 50ms between steps

    def _fade_in_step(self) -> None:
        """Perform one step of fade-in."""
        self._fade_step += 1
        volume = (self._fade_step / self._fade_steps) * self._target_volume
        self.audio_output.setVolume(min(volume, self._target_volume))
        
        if self._fade_step >= self._fade_steps:
            if self.fade_timer:
                self.fade_timer.stop()
            self.audio_output.setVolume(self._target_volume)

    def _start_fade_out(self, callback) -> None:
        """Start fade-out effect."""
        if self.fade_out_duration <= 0:
            callback()
            return
            
        if self.fade_timer:
            self.fade_timer.stop()
        
        steps = 20
        interval = int(self.fade_out_duration * 1000 / steps)
        self._fade_step = steps
        self._fade_steps = steps
        self._fade_callback = callback
        
        self.fade_timer = QTimer(self)
        self.fade_timer.timeout.connect(self._fade_out_step)
        self.fade_timer.start(max(interval, 50))

    def _fade_out_step(self) -> None:
        """Perform one step of fade-out."""
        self._fade_step -= 1
        volume = (self._fade_step / self._fade_steps) * self._target_volume
        self.audio_output.setVolume(max(volume, 0))
        
        if self._fade_step <= 0:
            if self.fade_timer:
                self.fade_timer.stop()
            self.audio_output.setVolume(0)
            if self._fade_callback:
                self._fade_callback()

    def toggle_playback(self) -> None:
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        if self.player.source().isEmpty():
            if self.displayed_song_ids:
                self.play_song(self.displayed_song_ids[0])
            return
        self.player.play()

    def play_next(self) -> None:
        if not self.displayed_song_ids:
            return
        if self.current_song_index < 0:
            self.play_song(self.displayed_song_ids[0])
            return
        next_index = (self.current_song_index + 1) % len(self.displayed_song_ids)
        self.play_song(self.displayed_song_ids[next_index])

    def play_previous(self) -> None:
        if not self.displayed_song_ids:
            return
        
        current_position = self.player.position()
        current_time_seconds = current_position / 1000.0
        
        if self.current_song_index < 0:
            self.play_song(self.displayed_song_ids[0])
            return
        
        # If current position is more than 3 seconds, restart the current song
        if current_time_seconds > self.PREV_THRESHOLD_SECONDS:
            self.player.setPosition(0)
            return
        
        # Otherwise, go to the previous song
        previous_index = (self.current_song_index - 1) % len(self.displayed_song_ids)
        self.play_song(self.displayed_song_ids[previous_index])

    def on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")

    def on_position_changed(self, position: int) -> None:
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(position)
        self.position_label.setText(format_duration(position / 1000))

    def on_duration_changed(self, duration: int) -> None:
        self.seek_slider.setRange(0, duration)
        self.duration_label.setText(format_duration(duration / 1000))

    def on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Check queue first
            if self.queue_index >= 0 and self.queue_index < len(self.queue) - 1:
                self.queue_index += 1
                next_song_id = self.queue[self.queue_index]
                self._start_fade_out(lambda: self.play_song(next_song_id))
            else:
                # Play next from displayed playlist
                self._start_fade_out(self.play_next)

    def on_player_error(self, error: QMediaPlayer.Error, error_text: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        QMessageBox.warning(self, "Playback error", error_text or "This file could not be played.")

    # ============== Queue Management ==============

    def add_to_queue(self, song_ids: list[str]) -> None:
        """Add songs to the queue."""
        for song_id in song_ids:
            if song_id not in self.queue:
                self.queue.append(song_id)
        self.refresh_queue_view()

    def add_to_queue_single(self, song_id: str) -> None:
        """Add a single song to the queue."""
        if song_id not in self.queue:
            self.queue.append(song_id)
            self.refresh_queue_view()

    def play_from_queue(self, item: QListWidgetItem) -> None:
        """Play a song from the queue."""
        row = self.queue_list.row(item)
        if 0 <= row < len(self.queue):
            self.queue_index = row
            self.play_song(self.queue[row])

    def clear_queue(self) -> None:
        """Clear the queue."""
        self.queue.clear()
        self.queue_index = -1
        self.refresh_queue_view()

    def refresh_queue_view(self) -> None:
        """Refresh the queue list widget."""
        self.queue_list.clear()
        for song_id in self.queue:
            song = self.store.songs.get(song_id)
            if song:
                item = QListWidgetItem(f"{song.title} - {song.artist}")
                item.setData(Qt.ItemDataRole.UserRole, song_id)
                self.queue_list.addItem(item)

    # ============== Song Context Menu ==============

    def show_song_context_menu(self, pos) -> None:
        """Show context menu for songs."""
        selected_ids = self.current_selected_song_ids()
        if not selected_ids:
            return
        
        menu = QMenu(self)
        
        # Add to existing playlists
        add_to_playlist_menu = QMenu("Add to Playlist", menu)
        add_to_playlist_menu.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ArrowRight))
        
        for playlist in self.store.sorted_playlists():
            action = add_to_playlist_menu.addAction(playlist.name)
            action.triggered.connect(lambda checked, p=playlist: self.add_songs_to_playlist_by_id(selected_ids, p.id))
        
        add_to_playlist_menu.addSeparator()
        create_action = add_to_playlist_menu.addAction("Create New Playlist with Selected...")
        create_action.triggered.connect(lambda: self.create_playlist_with_songs(selected_ids))
        
        menu.addMenu(add_to_playlist_menu)
        
        # Add to queue submenu
        queue_menu = QMenu("Add to Queue", menu)
        queue_menu.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ArrowRight))
        add_to_queue_action = queue_menu.addAction("Add to Queue")
        add_to_queue_action.triggered.connect(lambda: self.add_to_queue(selected_ids))
        queue_menu.addSeparator()
        play_next_action = queue_menu.addAction("Play Next")
        play_next_action.triggered.connect(lambda: self.add_to_queue_start(selected_ids))
        menu.addMenu(queue_menu)
        
        # Remove from playlist (if not default)
        if self.current_playlist_id != DEFAULT_PLAYLIST_ID:
            menu.addSeparator()
            remove_action = menu.addAction("Remove from Playlist")
            remove_action.triggered.connect(self.remove_selected_songs)
        
        menu.exec(self.song_table.viewport().mapToGlobal(pos))

    def add_songs_to_playlist_by_id(self, song_ids: list[str], playlist_id: str) -> None:
        """Add songs to a specific playlist by ID."""
        self.store.add_songs_to_playlist(playlist_id, song_ids)
        if self.current_playlist_id == playlist_id:
            self.refresh_playlist_view()

    def create_playlist_with_songs(self, song_ids: list[str]) -> None:
        """Create a new playlist with selected songs."""
        # Use first song's title as default playlist name
        default_name = "New Playlist"
        if song_ids:
            first_song = self.store.songs.get(song_ids[0])
            if first_song:
                default_name = f"Playlist - {first_song.title}"
        
        name, ok = QInputDialog.getText(self, "New Playlist", "Playlist name:", text=default_name)
        if not ok or not name.strip():
            return
        
        playlist = self.store.create_playlist(name.strip())
        self.store.add_songs_to_playlist(playlist.id, song_ids)
        self.refresh_all()
        
        # Select the new playlist
        self.select_playlist(playlist.id)

    def add_to_queue_start(self, song_ids: list[str]) -> None:
        """Add songs to queue and start playing from them."""
        # Insert after current queue position
        insert_pos = self.queue_index + 1 if self.queue_index >= 0 else 0
        
        for i, song_id in enumerate(reversed(song_ids)):
            self.queue.insert(insert_pos, song_id)
        
        # If nothing playing, start with first queued song
        if self.player.source().isEmpty():
            if self.queue:
                self.queue_index = insert_pos
                self.play_song(self.queue[insert_pos])
        
        self.refresh_queue_view()

    # ============== Settings Dialog ==============

    def show_settings(self) -> None:
        """Show settings dialog."""
        dialog = SettingsDialog(self, self.fade_in_duration, self.fade_out_duration)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.fade_in_duration = dialog.fade_in_value
            self.fade_out_duration = dialog.fade_out_value
            self.save_settings()


class SettingsDialog(QDialog):
    """Settings dialog for fade in/out configuration."""
    
    def __init__(self, parent: QWidget | None, current_fade_in: float, current_fade_out: float) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        # Fade settings
        fade_label = QLabel("Fade Settings")
        fade_label.setObjectName("SectionLabel")
        layout.addWidget(fade_label)
        
        # Fade in
        fade_in_layout = QHBoxLayout()
        fade_in_layout.addWidget(QLabel("Fade In Duration (seconds):"))
        self.fade_in_spin = QDoubleSpinBox()
        self.fade_in_spin.setRange(0, 30)
        self.fade_in_spin.setSingleStep(0.5)
        self.fade_in_spin.setDecimals(1)
        self.fade_in_spin.setValue(current_fade_in)
        fade_in_layout.addWidget(self.fade_in_spin)
        layout.addLayout(fade_in_layout)
        
        # Fade out
        fade_out_layout = QHBoxLayout()
        fade_out_layout.addWidget(QLabel("Fade Out Duration (seconds):"))
        self.fade_out_spin = QDoubleSpinBox()
        self.fade_out_spin.setRange(0, 30)
        self.fade_out_spin.setSingleStep(0.5)
        self.fade_out_spin.setDecimals(1)
        self.fade_out_spin.setValue(current_fade_out)
        fade_out_layout.addWidget(self.fade_out_spin)
        layout.addLayout(fade_out_layout)
        
        # Help text
        help_text = QLabel("Set to 0 for no fade effect. Fade out occurs when a song ends and the next one starts.")
        help_text.setObjectName("MutedLabel")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        
        layout.addStretch()
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.accept)
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(save_btn)
        layout.addLayout(button_layout)
    
    @property
    def fade_in_value(self) -> float:
        return self.fade_in_spin.value()
    
    @property
    def fade_out_value(self) -> float:
        return self.fade_out_spin.value()


STYLESHEET = """
QMainWindow, QWidget {
    background: #101318;
    color: #eff3f7;
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 14px;
}

QFrame#Sidebar {
    background: #151922;
    border-right: 1px solid #232936;
}

QLabel#AppTitle {
    color: #ffffff;
    font-size: 30px;
    font-weight: 800;
}

QLabel#SectionLabel,
QLabel#MutedLabel,
QLabel#DataHint {
    color: #95a1b2;
}

QLabel#DataHint {
    font-size: 11px;
}

QLineEdit {
    background: #191e28;
    color: #f7f9fc;
    border: 1px solid #2b3341;
    border-radius: 8px;
    padding: 9px 11px;
}

QLineEdit#PlaylistTitle {
    background: transparent;
    border: 0;
    color: #ffffff;
    font-size: 28px;
    font-weight: 750;
    padding: 0;
}

QLineEdit#PlaylistTitle:read-only {
    color: #ffffff;
}

QPushButton {
    background: #242b38;
    color: #eff3f7;
    border: 0;
    border-radius: 8px;
    padding: 10px 13px;
    font-weight: 650;
}

QPushButton:hover {
    background: #303847;
}

QPushButton:disabled {
    background: #1b202a;
    color: #687383;
}

QPushButton#PrimaryButton {
    background: #1ed760;
    color: #06120a;
}

QPushButton#PrimaryButton:hover {
    background: #35e170;
}

QPushButton[playlistTile="true"] {
    min-height: 48px;
    text-align: left;
    padding: 12px;
    background: #1b202b;
    color: #e8edf4;
}

QPushButton[playlistTile="true"]:hover {
    background: #263040;
}

QPushButton[playlistTile="true"][selected="true"] {
    background: #1ed760;
    color: #06120a;
}

QScrollArea {
    background: transparent;
    border: 0;
}

QTableWidget {
    background: #151922;
    border: 1px solid #252c39;
    border-radius: 8px;
    gridline-color: #242b37;
    selection-background-color: #1ed760;
    selection-color: #06120a;
}

QTableWidget::item {
    padding: 8px;
}

QHeaderView::section {
    background: #1b202b;
    color: #aab4c3;
    border: 0;
    border-bottom: 1px solid #283140;
    padding: 9px;
    font-weight: 700;
}

QFrame#PlayerBar {
    background: #151922;
    border: 1px solid #252c39;
    border-radius: 8px;
}

QLabel#NowPlaying {
    color: #ffffff;
    font-weight: 700;
}

QSlider::groove:horizontal {
    background: #2a3240;
    height: 5px;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #1ed760;
    width: 15px;
    margin: -5px 0;
    border-radius: 7px;
}

QListWidget {
    background: #151922;
    border: 1px solid #252c39;
    border-radius: 8px;
    padding: 6px;
}

QListWidget::item {
    padding: 10px;
    border-radius: 6px;
}

QListWidget::item:selected {
    background: #1ed760;
    color: #06120a;
}

QListWidget#QueueList {
    background: #1b202b;
    border: 1px solid #252c39;
    border-radius: 8px;
    padding: 6px;
    font-size: 12px;
}

QListWidget#QueueList::item {
    padding: 6px 8px;
    border-radius: 4px;
}

QListWidget#QueueList::item:hover {
    background: #263040;
}

QDoubleSpinBox {
    background: #191e28;
    color: #f7f9fc;
    border: 1px solid #2b3341;
    border-radius: 6px;
    padding: 6px 8px;
}

QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background: #303847;
    border-radius: 3px;
}

QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background: #3d4a5c;
}
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    
    # Required for system tray on some platforms
    app.setQuitOnLastWindowClosed(False)
    
    window = MainWindow()
    window.show()
    return app.exec()
