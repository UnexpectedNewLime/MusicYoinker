# MusicYoinker — simple SoundCloud downloader

This repository contains a small SoundCloud downloader script that reads playlist and track URLs from `playlists.txt` and downloads them using `yt-dlp`.

Files
- `soundcloud_downloader.py` — main script. Place it in the repo root and run from there.
- `playlists.txt` — example playlists file. The script reads `playlists.txt` in the current working directory.
- `requirements.txt` — python dependency list (yt-dlp).

playlists file format
- One URL per line.
- Blank lines and lines starting with `#` are ignored.
- The first meaningful line may begin with `args` followed by simple tokens to set defaults for the run.
  - Supported tokens in `args` (simple parsing):
    - `--format` or `-f` followed by `mp3` or `opus`
    - `--workers`, `--threads` or `-w` followed by a number (max 32)

Example `playlists.txt`
```
# use mp3 and 4 workers
args --format mp3 --workers 4
https://soundcloud.com/artist/sets/example-playlist-1
https://soundcloud.com/another_artist/sets/example-playlist-2
https://soundcloud.com/artist/example-track
```

Install

Windows PowerShell:

```powershell
python -m pip install -r requirements.txt
```

Make sure `ffmpeg` is installed and available on your PATH. On Windows, download a static build (for example from https://www.gyan.dev/ffmpeg/builds/) and add the `bin` folder to your PATH.

Run

```powershell
python .\soundcloud_downloader.py
```

The script will create a `downloads/` folder (in the current working directory) and write files there. After each run the script removes partial fragments (`.part`, `.temp`, files with `.part-` in the name).

Notes
- The script skips downloading tracks when a file with the same title (case-insensitive) already exists in the output folder.
- This is a small, opinionated tool. If you want CLI flags instead of the `args` line in the playlists file, or other features restored (progress hooks, metadata processing tweaks), say which and I'll add them.

Arguments (in playlists file)
--------------------------------
You can set default options for a run by adding an `args` line as the first meaningful line in `playlists.txt`.
The script parses the tokens after the leading `args` word with simple rules (no complex shell parsing).

Supported tokens:
- `--format` or `-f` <mp3|opus>
  - Output audio format. Default: `mp3`.
  - Example: `args --format opus` will request `opus` audio postprocessing.
- `--workers`, `--threads` or `-w` <N>
  - Number of concurrent download workers. Default: `min(8, cpu_count*2)`.
  - Example: `args --workers 4` or `args -w 4`.

Behavior notes:
- Unknown tokens are ignored.
- The script reads `playlists.txt` from the current working directory.
- The `args` line must come before any URLs. Only the first `args` line is considered.

Examples
```
args --format mp3 --workers 4
https://soundcloud.com/artist/sets/example-playlist-1
https://soundcloud.com/artist/example-track
```

If you prefer explicit CLI flags instead of the `args` line, tell me and I will add `argparse` support so you can pass `--format` and `--workers` directly to `soundcloud_downloader.py`.
