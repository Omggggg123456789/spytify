from __future__ import annotations

import json
import os
import uuid
import zipfile
from pathlib import Path
from typing import Iterable

from .importer import SUPPORTED_AUDIO_EXTENSIONS, convert_to_flac, hash_file, read_audio_metadata
from .models import DEFAULT_PLAYLIST_ID, Playlist, Song, utc_now_iso


LIBRARY_VERSION = 1
PLAYLIST_VERSION = 1


def default_data_dir() -> Path:
    override = os.getenv("SPYTIFY_HOME")
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / "Spytify"
        return Path.home() / "AppData" / "Roaming" / "Spytify"

    if sys_platform := os.getenv("XDG_DATA_HOME"):
        return Path(sys_platform) / "spytify"
    return Path.home() / ".local" / "share" / "spytify"


class LibraryStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_data_dir()
        self.music_dir = self.root / "music"
        self.playlists_dir = self.root / "playlists"
        self.library_path = self.root / "library.json"
        self.songs: dict[str, Song] = {}
        self.playlists: dict[str, Playlist] = {}
        self.ensure_dirs()
        self.load()

    def ensure_dirs(self) -> None:
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.playlists_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        self.songs = self._load_songs()
        self.playlists = self._load_playlists()

    def _load_songs(self) -> dict[str, Song]:
        if not self.library_path.exists():
            return {}
        try:
            payload = json.loads(self.library_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        songs: dict[str, Song] = {}
        for raw_song in payload.get("songs", []):
            song = Song.from_dict(raw_song)
            if song.id and song.file_name:
                songs[song.id] = song
        return songs

    def _load_playlists(self) -> dict[str, Playlist]:
        playlists: dict[str, Playlist] = {}
        for path in sorted(self.playlists_dir.glob("*.spyt")):
            try:
                with zipfile.ZipFile(path, "r") as archive:
                    raw = archive.read("playlist.json")
                payload = json.loads(raw.decode("utf-8"))
            except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
                continue

            playlist = Playlist.from_dict(payload.get("playlist", payload))
            if playlist.id and playlist.id != DEFAULT_PLAYLIST_ID:
                playlist.song_ids = [song_id for song_id in playlist.song_ids if song_id in self.songs]
                playlists[playlist.id] = playlist
        return playlists

    def save_library(self) -> None:
        payload = {
            "version": LIBRARY_VERSION,
            "songs": [song.to_dict() for song in self.sorted_songs()],
        }
        temp_path = self.library_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(self.library_path)

    def sorted_songs(self) -> list[Song]:
        return sorted(self.songs.values(), key=lambda song: (song.imported_at, song.title.lower()))

    def sorted_playlists(self) -> list[Playlist]:
        return sorted(self.playlists.values(), key=lambda playlist: playlist.name.lower())

    def song_path(self, song: Song) -> Path:
        return self.music_dir / song.file_name

    def create_playlist(self, name: str) -> Playlist:
        playlist = Playlist(id=uuid.uuid4().hex, name=name.strip() or "Untitled Playlist")
        self.playlists[playlist.id] = playlist
        self.save_playlist(playlist)
        return playlist

    def rename_playlist(self, playlist_id: str, new_name: str) -> Playlist | None:
        playlist = self.playlists.get(playlist_id)
        if playlist is None:
            return None
        playlist.name = new_name.strip() or "Untitled Playlist"
        playlist.updated_at = utc_now_iso()
        self.save_playlist(playlist)
        return playlist

    def delete_playlist(self, playlist_id: str) -> None:
        self.playlists.pop(playlist_id, None)
        path = self.playlist_path(playlist_id)
        if path.exists():
            path.unlink()

    def add_songs_to_playlist(self, playlist_id: str, song_ids: Iterable[str]) -> Playlist | None:
        playlist = self.playlists.get(playlist_id)
        if playlist is None:
            return None
        changed = False
        for song_id in song_ids:
            if song_id in self.songs and song_id not in playlist.song_ids:
                playlist.song_ids.append(song_id)
                changed = True
        if changed:
            playlist.updated_at = utc_now_iso()
            self.save_playlist(playlist)
        return playlist

    def remove_songs_from_playlist(self, playlist_id: str, song_ids: Iterable[str]) -> Playlist | None:
        playlist = self.playlists.get(playlist_id)
        if playlist is None:
            return None
        remove_ids = set(song_ids)
        original_count = len(playlist.song_ids)
        playlist.song_ids = [song_id for song_id in playlist.song_ids if song_id not in remove_ids]
        if len(playlist.song_ids) != original_count:
            playlist.updated_at = utc_now_iso()
            self.save_playlist(playlist)
        return playlist

    def playlist_path(self, playlist_id: str) -> Path:
        return self.playlists_dir / f"{playlist_id}.spyt"

    def save_playlist(self, playlist: Playlist) -> None:
        payload = {
            "version": PLAYLIST_VERSION,
            "playlist": playlist.to_dict(),
        }
        path = self.playlist_path(playlist.id)
        temp_path = path.with_suffix(".tmp")
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("playlist.json", json.dumps(payload, indent=2))
        temp_path.replace(path)

    def import_music_file(self, source: Path) -> tuple[Song, bool]:
        source = source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            raise ValueError(f"{source.name} is not a supported audio file.")

        source_hash = hash_file(source)
        for song in self.songs.values():
            if song.source_hash == source_hash:
                return song, False

        song_id = uuid.uuid4().hex
        file_name = f"{song_id}.flac"
        target = self.music_dir / file_name
        convert_to_flac(source, target)
        title, artist, album, duration = read_audio_metadata(target)
        if title == target.stem:
            title, artist, album, duration = read_audio_metadata(source)

        song = Song(
            id=song_id,
            title=title,
            artist=artist,
            album=album,
            file_name=file_name,
            source_path=str(source),
            source_hash=source_hash,
            duration_seconds=duration,
        )
        self.songs[song.id] = song
        self.save_library()
        return song, True
