from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


DEFAULT_PLAYLIST_ID = "all-imported"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Song:
    id: str
    title: str
    artist: str
    album: str
    file_name: str
    source_path: str
    source_hash: str
    duration_seconds: float = 0.0
    imported_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Song":
        return cls(
            id=str(payload.get("id", "")),
            title=str(payload.get("title") or "Unknown Title"),
            artist=str(payload.get("artist") or "Unknown Artist"),
            album=str(payload.get("album") or "Unknown Album"),
            file_name=str(payload.get("file_name", "")),
            source_path=str(payload.get("source_path", "")),
            source_hash=str(payload.get("source_hash", "")),
            duration_seconds=float(payload.get("duration_seconds") or 0.0),
            imported_at=str(payload.get("imported_at") or utc_now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Playlist:
    id: str
    name: str
    song_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    cover_image: str | None = None  # Base64 encoded cover image from first song

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Playlist":
        raw_song_ids = payload.get("song_ids") or []
        song_ids = [str(song_id) for song_id in raw_song_ids if song_id]
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name") or "Untitled Playlist"),
            song_ids=song_ids,
            created_at=str(payload.get("created_at") or utc_now_iso()),
            updated_at=str(payload.get("updated_at") or utc_now_iso()),
            cover_image=payload.get("cover_image"),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        return result
