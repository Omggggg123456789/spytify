from __future__ import annotations

import base64
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

# Compression level for embedded audio (6 = balanced, 1 = fastest, 9 = best compression)
PLAYLIST_AUDIO_COMPRESSION = 6


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

    def save_playlist(self, playlist: Playlist, embed_songs: bool = True) -> None:
        payload = {
            "version": PLAYLIST_VERSION,
            "playlist": playlist.to_dict(),
        }
        path = self.playlist_path(playlist.id)
        temp_path = path.with_suffix(".tmp")
        
        # Extract cover from first song if playlist has no cover yet
        if not playlist.cover_image and playlist.song_ids:
            first_song = self.songs.get(playlist.song_ids[0])
            if first_song:
                song_path = self.song_path(first_song)
                if song_path.exists():
                    cover = self._extract_cover_image(song_path)
                    if cover:
                        playlist.cover_image = cover
        
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=PLAYLIST_AUDIO_COMPRESSION) as archive:
            archive.writestr("playlist.json", json.dumps(payload, indent=2))
            
            # Embed songs in the zip for offline playback
            if embed_songs:
                for song_id in playlist.song_ids:
                    song = self.songs.get(song_id)
                    if song:
                        song_path = self.song_path(song)
                        if song_path.exists():
                            archive.write(song_path, f"songs/{song_id}.flac")
        
        temp_path.replace(path)

    def _extract_cover_image(self, audio_path: Path) -> str | None:
        """Extract cover image from audio file and return as base64 string."""
        try:
            from mutagen import File as MutagenFile
        except Exception:
            return None
        
        try:
            audio = MutagenFile(str(audio_path))
            if audio is None or not audio.tags:
                return None
            
            # Try to find album art (cover)
            for key in ['APIC:', 'cover', 'albumart']:
                try:
                    picture = audio.tags.get(key)
                    if picture:
                        # For Mutagen's Picture type
                        if hasattr(picture, 'data'):
                            return base64.b64encode(picture.data).decode('ascii')
                        # For CoverImage or other types
                        elif hasattr(picture, 'value') and isinstance(picture.value, bytes):
                            return base64.b64encode(picture.value).decode('ascii')
                except (KeyError, AttributeError, TypeError):
                    continue
            
            # Try generic approach
            for tag in audio.tags.values():
                if hasattr(tag, 'data') and isinstance(tag.data, bytes):
                    mime = getattr(tag, 'mime', 'image/jpeg')
                    if mime.startswith('image/'):
                        return base64.b64encode(tag.data).decode('ascii')
        except Exception:
            pass
        
        return None

    def get_playlist_song_path(self, playlist_id: str, song_id: str) -> Path | None:
        """Get the path to a song, checking embedded playlist first, then library."""
        playlist = self.playlists.get(playlist_id)
        if playlist is None:
            return None
        
        # First check if song is embedded in the playlist zip
        playlist_zip_path = self.playlist_path(playlist_id)
        if playlist_zip_path.exists():
            try:
                with zipfile.ZipFile(playlist_zip_path, "r") as archive:
                    embedded_path = f"songs/{song_id}.flac"
                    if embedded_path in archive.namelist():
                        # Extract to temp for playback
                        temp_dir = self.playlists_dir / ".temp"
                        temp_dir.mkdir(exist_ok=True)
                        temp_path = temp_dir / f"{song_id}.flac"
                        
                        # Check if already extracted and not older than source
                        source_path = self.song_path(self.songs.get(song_id, Song("", "", "", "", "", "", 0.0)))
                        if not temp_path.exists() or (source_path.exists() and temp_path.stat().st_mtime < source_path.stat().st_mtime):
                            with archive.open(embedded_path) as src, open(temp_path, 'wb') as dst:
                                dst.write(src.read())
                        return temp_path
            except (zipfile.BadZipFile, KeyError, OSError):
                pass
        
        # Fall back to library path
        song = self.songs.get(song_id)
        if song:
            path = self.song_path(song)
            if path.exists():
                return path
        
        return None
    
    def clear_temp_files(self) -> None:
        """Clean up temporary extracted files."""
        temp_dir = self.playlists_dir / ".temp"
        if temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir)

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
