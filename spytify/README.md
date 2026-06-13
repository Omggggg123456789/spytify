# Spytify

Spytify is a lightweight local Spotify-style desktop player written in Python with PySide6. It imports audio from your filesystem, converts imported tracks to FLAC, stores them in a dedicated Spytify music folder, and lets you build editable local playlists.

## Features

- Modern PySide6 desktop UI, no Tkinter.
- Scrollable playlist tiles in the left sidebar.
- Default `All Imported` playlist that automatically contains every imported song.
- Create, rename, and delete playlists.
- Import audio with a file picker.
- Converts imported music to `.flac` and stores it in the Spytify library folder.
- Saves user playlists as zipped `.spyt` files.
- Play, pause, skip, seek, and adjust volume.
- Add already imported songs to any user playlist.

## Requirements

- Python 3.10 or newer.
- FFmpeg for converting non-FLAC files.

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the app:

```powershell
python spytify_app.py
```

## FFmpeg

Spytify looks for FFmpeg in this order:

1. `ffmpeg` on PATH.
2. `ffmpeg.exe` next to a packaged `Spytify.exe`.
3. A bundled `ffmpeg.exe` inside the PyInstaller one-file build.
4. `ffmpeg.exe` in the current working directory.

If FFmpeg is missing, existing `.flac` files can still be imported, but other formats cannot be converted.

To bundle FFmpeg into the one-file build, place `ffmpeg.exe` at:

```text
tools\ffmpeg.exe
```

Then run `build.bat`.

## Build One EXE

```bat
build.bat
```

The output is:

```text
dist\Spytify.exe
```

## Where Data Is Stored

By default on Windows:

```text
%APPDATA%\Spytify
```

Inside that folder:

- `music\` contains imported FLAC files.
- `library.json` contains song metadata.
- `playlists\*.spyt` contains user playlist zip files.

You can override the library location with:

```powershell
$env:SPYTIFY_HOME = "D:\MySpytifyLibrary"
python spytify_app.py
```
