"""
Minimal SoundCloud downloader
Reads playlist/track URLs from `playlists.conf` or `playlists.txt` (one URL per line).
The first non-empty, non-comment line may start with `args` followed by space-separated options
that are applied as default CLI options for the run (example: "args --format opus --workers 4").
Blank lines and lines starting with `#` are ignored.
Skips existing songs by filename (case-insensitive title match).
Uses yt-dlp to extract playlist entries and download tracks concurrently.
"""

import os
import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Set

try:
    import yt_dlp
except Exception as e:
    print("yt-dlp is required. Install with: pip install -r requirements.txt")
    raise

# Logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("soundcloud_downloader")

# Defaults
DEFAULT_OUTDIR = "downloads"
MAX_WORKERS = min(8, (os.cpu_count() or 1) * 2)
SUPPORTED_EXTS = ('.mp3', '.opus', '.m4a')


def read_playlists_file(path: str):
    """Read file and return (args_list, url_list).
    args_list is a list of strings (tokens) from a line that starts with 'args'.
    url_list is the list of URLs to process.
    """
    if not os.path.exists(path):
        return [], []
    urls = []
    args_tokens = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith('#'):
                continue
            # First meaningful line may contain args
            if raw.lower().startswith('args') and not args_tokens:
                parts = raw.split()
                # tokens after the word 'args'
                args_tokens = parts[1:]
                continue
            urls.append(raw)
    return args_tokens, urls


def get_existing_titles(outdir: str) -> Set[str]:
    os.makedirs(outdir, exist_ok=True)
    titles = set()
    for fn in os.listdir(outdir):
        if fn.lower().endswith(SUPPORTED_EXTS):
            titles.add(os.path.splitext(fn)[0].lower())
    return titles


def cleanup_partials(outdir: str) -> int:
    """Remove partial download fragments (.part, .temp, .part-*) and return count removed."""
    removed = 0
    for fn in os.listdir(outdir):
        if fn.endswith('.part') or fn.endswith('.temp') or '.part-' in fn:
            try:
                os.remove(os.path.join(outdir, fn))
                removed += 1
            except OSError:
                pass
    return removed


def expand_playlist(url: str) -> List[str]:
    ydl_opts = { 'quiet': True, 'extract_flat': True }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return []
            # If it's a playlist, entries will exist
            if 'entries' in info and info['entries']:
                return [entry.get('url') or entry.get('webpage_url') for entry in info['entries'] if entry]
            # Single track URL
            return [url]
    except Exception as e:
        log.error(f"Failed to expand playlist {url}: {e}")
        return []


def download_track(url: str, outdir: str, existing: Set[str], audio_format: str = 'mp3') -> bool:
    # First get info to determine title
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        log.error(f"Failed to get info for {url}: {e}")
        return False

    title = (info.get('title') or '').strip()
    if not title:
        log.error(f"No title for {url}")
        return False

    if title.lower() in existing:
        log.info(f"Skipping existing: {title}")
        return True

    # Setup yt-dlp options
    out_template = os.path.join(outdir, '%(title)s.%(ext)s')
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': audio_format,
            'preferredquality': '192',
        }],
        'prefer_ffmpeg': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        # Add to existing set to prevent duplicates in same run
        existing.add(title.lower())
        log.info(f"Downloaded: {title}")
        return True
    except Exception as e:
        log.error(f"Failed to download {title}: {e}")
        return False



def main():
    # Determine input file (use playlists.txt only)
    cwd = os.getcwd()
    candidates = [os.path.join(cwd, 'playlists.txt')]
    args_tokens = []
    urls = []
    source_file = None
    for c in candidates:
        if os.path.exists(c):
            args_tokens, urls = read_playlists_file(c)
            source_file = c
            if urls or args_tokens:
                log.info(f"Read {len(urls)} entries from {os.path.basename(c)}")
                break

    if not urls:
        log.error("No playlists file found or file contains no URLs. Create playlists.conf or playlists.txt with one URL per line.")
        sys.exit(1)

    # Parse simple args tokens for default behavior: support --format and --workers
    audio_format = 'mp3'
    workers = MAX_WORKERS
    try:
        it = iter(args_tokens)
        for t in it:
            if t in ('--format', '-f'):
                audio_format = next(it, audio_format)
            elif t in ('--workers', '--threads', '-w'):
                w = next(it, None)
                if w and w.isdigit():
                    workers = max(1, min(int(w), 32))
    except Exception:
        pass

    outdir = os.path.join(cwd, DEFAULT_OUTDIR)
    existing = get_existing_titles(outdir)

    # Expand all playlists/urls into track URLs
    all_tracks = []
    for u in urls:
        expanded = expand_playlist(u)
        if expanded:
            all_tracks.extend(expanded)

    if not all_tracks:
        log.error("No tracks found to download.")
        sys.exit(1)

    log.info(f"Starting downloads: {len(all_tracks)} tracks (skipping {len(existing)} existing files). Workers={workers} format={audio_format}")

    # Concurrent download
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = { ex.submit(download_track, t, outdir, existing, audio_format): t for t in all_tracks }
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                fut.result()
            except Exception as e:
                log.error(f"Error downloading {url}: {e}")

    # Cleanup partial files
    removed = cleanup_partials(outdir)
    if removed:
        log.info(f"Removed {removed} partial files from {outdir}")

    log.info("All done.")


if __name__ == '__main__':
    main()
