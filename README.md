# MusicYoinker — simple SoundCloud downloader

Usage
-----
Follow these steps to run the downloader from a phone with Termux and add it to the home-screen widget:

1) Pull the repo on a phone with Termux installed

  In Termux:

```bash
git clone <your-repo-url>
cd MusicYoinker
```

2) Move the runner into the Termux shortcuts directory (so it can be added to the Home-screen widget)

```bash
mv download_soundcloud.playlists.sh ~/.shortcuts/
chmod +x ~/.shortcuts/download_soundcloud.playlists.sh
```

3) Set environment variables (examples)

  The script reads a playlists file and needs a base output directory and a log directory. Example environment variables you can set in your Termux shell or ~/.profile:

```bash
export PLAYLISTS_FILE="$HOME/playlists.txt"          # path to playlist file (required)
export SOUNDCLOUD_BASE_DIR="$HOME/Music"  # where to store downloaded music
export SOUNDCLOUD_LOG_DIR="$HOME/logs"               # where per-playlist logs go
export PY_DOWNLOADER="$HOME/MusicYoinker/SoundCloud_Downloader.py"  # path to downloader script
```

4) Add to the Termux home-screen widget

  After moving the script into `~/.shortcuts` it should appear in the Termux shortcuts widget. Tap the script name to run it; the runner will read the file pointed to by `PLAYLISTS_FILE` and download each playlist to `SOUNDCLOUD_BASE_DIR`.

Overview
--------
This repo contains a small runner script and a Python downloader:

- `download_soundcloud.playlists.sh` — Termux-friendly runner. Reads playlist URLs from a required `playlists.txt` (or file set by `PLAYLISTS_FILE`) and runs the downloader once per playlist, writing one log file per playlist and keeping the 3 most recent logs for each playlist.
- `SoundCloud_Downloader.py` — Python downloader that uses `yt-dlp` + `ffmpeg` to download/convert audio. It supports concurrent downloads and will skip tracks that already exist (case-insensitive title match).

Requirements
------------
- Python 3.8+
- `yt-dlp` (install with pip)
- `ffmpeg` binary on PATH

Install (Termux / Linux)

```bash
python -m pip install --upgrade yt-dlp
# ensure ffmpeg is installed via your package manager (Termux: pkg install ffmpeg)
```

Playlists file format
---------------------
- One URL per line.
- Blank lines and lines starting with `#` are ignored.

Example `playlists.txt`

```text
# one SoundCloud playlist or track URL per line
https://soundcloud.com/artist/sets/example-playlist-1
https://soundcloud.com/artist/example-track
```

Running the runner manually
--------------------------
If you prefer not to use the Termux widget, run the helper directly:

```bash
# ensure PLAYLISTS_FILE is set or playlists.txt exists in cwd
./download_soundcloud.playlists.sh /path/to/base_dir /path/to/log_dir
```

Run directly from the Python script
----------------------------------
You can also run the downloader directly without the runner. This runs a single playlist URL and creates a subfolder named after the playlist title inside the output directory:

```bash
# Single playlist URL -> output dir
python3 SoundCloud_Downloader.py "https://soundcloud.com/artist/sets/example-playlist-1" /path/to/output_base_dir
```

Notes:
- The Python script will create a subfolder under the output directory named after the playlist title (sanitized) and place downloaded tracks inside it.
- The script checks for `yt-dlp` and `ffmpeg` and will attempt a best-effort install on some platforms, but it's recommended to install them manually.
