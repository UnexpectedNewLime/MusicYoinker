# MusicYoinker — simple SoundCloud downloader

Usage
-----
Follow these steps to run the downloader from a phone with Termux and add it to the home-screen widget:

Download Termux from the play store:
https://play.google.com/store/apps/details?id=com.termux&hl=en_AU&pli=1

1) Pull the repo on a phone with Termux installed

  In Termux:

```bash
git clone <your-repo-url>
cd MusicYoinker
```

2) Move the runner into the Termux shortcuts directory (so it can be added to the Home-screen widget)

```bash
mv download_soundcloud.playlists.sh ~/.shortcuts/
cd ~/.shortcuts/
chmod +x ~/.shortcuts/download_soundcloud.playlists.sh
```

3) Set environment variables (examples)

  The script reads a playlists file and needs a base output directory and a log directory. Example environment variables you can set in your Termux shell or ~/.bashrc:
  
```bash
export PLAYLISTS_FILE="$HOME/playlists.txt"          # path to playlist file (required)
export SOUNDCLOUD_BASE_DIR="$HOME/Music"  # where to store downloaded music
export SOUNDCLOUD_LOG_DIR="$HOME/logs"               # where per-playlist logs go
export PY_DOWNLOADER="$HOME/MusicYoinker/SoundCloud_Downloader.py"  # path to downloader script
```

  Optional settings for tuning rate-limit handling (defaults shown, see [Rate limits and resuming](#rate-limits-and-resuming)):

```bash
export SOUNDCLOUD_PLAYLIST_DELAY=60       # seconds to wait between playlists
export SOUNDCLOUD_RATE_LIMIT_WAIT=300     # seconds to wait when SoundCloud rate limits us before resuming
export SOUNDCLOUD_MAX_IDLE_WAITS=3        # give up after this many rate-limit waits in a row with no new downloads
```
Add these to the end of of your .bashrc file via:
```bash
cd ~
nano .bashrc
```

4) Download the required dependencies

  The script requires python3 and yt-dlp to work so they must be installed onto the device:
  
```bash
pkg install python3 -y
pkg install yt-dlp -y
```
5) Create a playlists file:
   This is the file where playlists will be read from, ensure that the soundcloud playlist is public
```bash
cd ~  #to create the playlist file at the location we defined in the env vars above PLAYLISTS_FILE
nano playlists.txt
# once the nano window is open you can paste the playlists in to the file ensuring each playlist has its own line check playlists.example.txt in the repo for an example
```
    
6) run via the Termux home-screen widget

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

Rate limits and resuming
------------------------
SoundCloud starts returning HTTP 403 after roughly 120 track downloads in a few minutes. The runner is built so that a big first download can still finish in one go:

- **Already-downloaded tracks cost no API calls.** Each playlist folder has a `.downloaded_ids` file listing the ID and filename of each track already downloaded; tracks whose file is still there are skipped before any request is made, and tracks whose file was deleted or moved are downloaded again. Tracks you already had before this file existed are matched by title on the first run (one request each) and then recorded. Entries written by older versions have no filename and are trusted as-is; delete a folder's `.downloaded_ids` to re-check it.
- **Most-incomplete playlists go first.** The base directory has a `.playlist_index.json` recording each playlist's folder and track count from its last fetch, so the runner can sort playlists by % still missing without any requests. Playlists that have never been fetched count as 100% missing.
- **It stops hammering once rate limited.** After 3 HTTP 403s in a row the downloader re-requests a track it knows is accessible (the last one that succeeded, or one downloaded on an earlier run; the playlist itself as a last resort) to confirm. If that is refused too, SoundCloud is throttling, so it stops sending requests and exits with code 3; if it succeeds, the 403s were for individual inaccessible tracks (e.g. private ones) and it carries on.
- **It waits and resumes.** On exit code 3 the runner waits `SOUNDCLOUD_RATE_LIMIT_WAIT` seconds (default 5 minutes) and retries the same playlist. It gives up after `SOUNDCLOUD_MAX_IDLE_WAITS` waits in a row that downloaded nothing new; just run it again later to continue.
- **The phone stays awake.** On Termux the runner holds `termux-wake-lock` while it runs, since waits can stretch a large first download to 30+ minutes.

DRM-protected and geo-restricted tracks can never be downloaded; they show up as errors in the logs and are retried on each run.

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
