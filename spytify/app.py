from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QKeySequence
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
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

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player.setAudioOutput(self.audio_output)

        self.setWindowTitle(__app_name__)
        self.resize(1180, 760)
        self.setMinimumSize(920, 620)

        self.build_ui()
        self.bind_player()
        self.install_shortcuts()
        self.refresh_all()

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
        button.clicked.connect(lambda checked=False, selected_id=playlist_id: self.select_playlist(selected_id))
        if playlist is not None:
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, selected_id=playlist_id, source=button: self.show_playlist_menu(selected_id, source, pos)
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
        song_path = self.store.song_path(song)
        if not song_path.exists():
            QMessageBox.warning(self, "Missing file", f"Could not find {song_path}")
            return
        self.current_song_index = self.displayed_song_ids.index(song_id) if song_id in self.displayed_song_ids else -1
        self.player.setSource(QUrl.fromLocalFile(str(song_path)))
        self.player.play()
        self.now_playing_label.setText(f"{song.title}\n{song.artist}")

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
        if self.current_song_index < 0:
            self.play_song(self.displayed_song_ids[0])
            return
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
            self.play_next()

    def on_player_error(self, error: QMediaPlayer.Error, error_text: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        QMessageBox.warning(self, "Playback error", error_text or "This file could not be played.")


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
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    window = MainWindow()
    window.show()
    return app.exec()
