from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path


SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}


class ImportFailure(RuntimeError):
    """Raised when an audio file cannot be imported into Spytify."""


def find_ffmpeg() -> str | None:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).with_name("ffmpeg.exe"))
        bundle_dir = Path(getattr(sys, "_MEIPASS", ""))
        candidates.append(bundle_dir / "ffmpeg.exe")
    candidates.append(Path.cwd() / "ffmpeg.exe")

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def convert_to_flac(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = find_ffmpeg()
    if source.suffix.lower() == ".flac" and ffmpeg is None:
        shutil.copy2(source, destination)
        return

    if ffmpeg is None:
        raise ImportFailure(
            "FFmpeg was not found. Install FFmpeg or place ffmpeg.exe next to Spytify to import non-FLAC audio."
        )

    temp_destination = destination.with_suffix(".tmp.flac")
    if temp_destination.exists():
        temp_destination.unlink()

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-map_metadata",
        "0",
        "-c:a",
        "flac",
        str(temp_destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if temp_destination.exists():
            temp_destination.unlink()
        message = result.stderr.strip() or "FFmpeg could not convert this file."
        raise ImportFailure(message)

    temp_destination.replace(destination)


def read_audio_metadata(path: Path) -> tuple[str, str, str, float]:
    title = path.stem
    artist = "Unknown Artist"
    album = "Unknown Album"
    duration = 0.0

    try:
        from mutagen import File as MutagenFile
    except Exception:
        return title, artist, album, duration

    try:
        audio = MutagenFile(str(path), easy=True)
    except Exception:
        return title, artist, album, duration

    if audio is None:
        return title, artist, album, duration

    def first_tag(key: str, fallback: str) -> str:
        values = audio.tags.get(key) if audio.tags else None
        if values:
            value = str(values[0]).strip()
            if value:
                return value
        return fallback

    title = first_tag("title", title)
    artist = first_tag("artist", artist)
    album = first_tag("album", album)

    info = getattr(audio, "info", None)
    if info is not None:
        duration = float(getattr(info, "length", 0.0) or 0.0)

    return title, artist, album, duration
