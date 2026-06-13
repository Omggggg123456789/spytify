@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH.
    exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 exit /b %errorlevel%

python -m pip install -r requirements.txt
if errorlevel 1 exit /b %errorlevel%

set "FFMPEG_ARGS="
if exist "tools\ffmpeg.exe" (
    echo Including tools\ffmpeg.exe in the one-file build.
    set "FFMPEG_ARGS=--add-binary=tools\ffmpeg.exe;."
) else (
    echo tools\ffmpeg.exe was not found. The app will still build, but non-FLAC imports need FFmpeg on PATH.
)

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name Spytify ^
    %FFMPEG_ARGS% ^
    spytify_app.py

if errorlevel 1 exit /b %errorlevel%

echo.
echo Built dist\Spytify.exe
