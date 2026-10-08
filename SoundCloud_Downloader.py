#!/usr/bin/env python3
"""
SoundCloud Script

Description:
    This script is used to download songs from SoundCloud.
    Features:
    - Concurrent downloads
    - Multiple audio formats (mp3, opus)
    - Duplicate detection
    - Cleanup utilities
    - Progress tracking

Author: Lime
Date: 17/05/2025
"""

import os
import sys
import json
import logging
import subprocess
import argparse
import platform
from typing import Optional, List, Set, Dict, Any, Tuple
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
from threading import Lock, Event

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(f"SoundCloud-Downloader.{__name__}")

# Number of concurrent downloads (adjust based on your connection)
MAX_CONCURRENT_DOWNLOADS = multiprocessing.cpu_count() * 2

# Track which files we're currently downloading to prevent duplicate messages
downloading_tracks = set()
downloading_lock = Lock()

# Per-playlist file of already-downloaded track IDs, so re-runs need no per-track API calls
ARCHIVE_FILENAME = '.downloaded_ids'
archive_lock = Lock()

# Stop after this many consecutive HTTP 403s: SoundCloud is rate limiting us, and every
# further request just extends the block. Archived progress lets the next run resume.
RATE_LIMIT_403_THRESHOLD = 3
RATE_LIMITED_EXIT_CODE = 3
consecutive_403s = 0
probing_rate_limit = False
rate_limit_lock = Lock()
rate_limited = Event()
# URL known to have just worked (the playlist being downloaded), used to tell throttling
# apart from 403s on individual tracks that are simply inaccessible (e.g. private)
probe_url: Optional[str] = None

class RateLimitedError(Exception):
    """Raised when SoundCloud keeps answering with HTTP 403."""

def is_rate_limit_error(e: BaseException) -> bool:
    return 'HTTP Error 403' in str(e)

def is_throttled() -> bool:
    """
    Re-request the probe URL once. A 403 there means SoundCloud is throttling us as a whole;
    success means the preceding 403s were for individual inaccessible tracks.
    Any failure to probe is treated as throttling, to err on the side of backing off.
    """
    import yt_dlp
    if not probe_url:
        return True
    try:
        with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True}) as ydl:
            return not ydl.extract_info(probe_url, download=False)
    except Exception as e:
        logger.debug(f"Rate-limit probe failed: {type(e).__name__}: {e}")
        return True

def note_request_result(got_403: bool) -> None:
    """
    Track consecutive 403s across worker threads. At the threshold, probe once to confirm
    SoundCloud is throttling before tripping the rate-limit flag.
    """
    global consecutive_403s, probing_rate_limit
    with rate_limit_lock:
        if not got_403:
            consecutive_403s = 0
            return
        consecutive_403s += 1
        if consecutive_403s < RATE_LIMIT_403_THRESHOLD or rate_limited.is_set() or probing_rate_limit:
            return
        probing_rate_limit = True
        count = consecutive_403s

    # Probe outside the lock so other workers aren't blocked on a network request
    try:
        throttled = is_throttled()
    finally:
        with rate_limit_lock:
            probing_rate_limit = False

    with rate_limit_lock:
        if throttled:
            logger.error(f"Got {count} HTTP 403s in a row and a probe request was also refused; SoundCloud is rate limiting. Stopping remaining downloads.")
            rate_limited.set()
        else:
            logger.warning(f"Got {count} HTTP 403s in a row, but a probe request succeeded; treating them as inaccessible tracks and continuing.")
            consecutive_403s = 0

# Base-dir file mapping playlist URL -> {folder, total, ids} from the last successful fetch,
# so the runner can order playlists by how much is missing without any API calls
INDEX_FILENAME = '.playlist_index.json'

def get_existing_songs(directory: str) -> Set[str]:
    """
    Get a set of existing song titles (without extension) in the directory.
    """
    existing_songs = set()
    for file in os.listdir(directory):
        if file.endswith(('.mp3', '.opus', '.m4a')):  # Support multiple formats
            # Remove the extension to get just the title
            title = os.path.splitext(file)[0]
            existing_songs.add(title.lower())  # Store lowercase for case-insensitive comparison
    return existing_songs

def clean_partial_downloads(directory: str, basename: str = None) -> Tuple[int, int]:
    """
    Clean up any partial download fragments.
    If basename is None, clean all partial downloads in directory.
    Returns tuple of (cleaned_count, failed_count)
    """
    cleaned_count = 0
    failed_count = 0
    
    def should_clean(file: str) -> bool:
        if basename:
            base_without_ext = os.path.splitext(basename)[0]
            return file.startswith(base_without_ext) and (
                file.endswith('.part') or 
                file.endswith('.temp') or 
                file.endswith('.opus') or 
                file.endswith('.m4a') or
                '.part-' in file
            )
        return (file.endswith('.part') or 
                file.endswith('.temp') or 
                '.part-' in file)

    for file in os.listdir(directory):
        if should_clean(file):
            try:
                os.remove(os.path.join(directory, file))
                logger.info(f"Cleaned up fragment: {file}")
                cleaned_count += 1
            except OSError as e:
                logger.debug(f"Failed to clean up {file}: {e}")
                failed_count += 1

    if cleaned_count == 0 and failed_count == 0:
        logger.info("No partial downloads found to clean.")
    else:
        logger.info(f"Cleanup complete. Cleaned: {cleaned_count}, Failed: {failed_count}")
    
    return cleaned_count, failed_count

def download_progress_hook(d: dict, existing_songs: Set[str], output_dir: str, progress_callback=None) -> None:
    """
    Progress hook for yt-dlp that checks for existing files.
    """
    global downloading_tracks
    
    if d['status'] == 'downloading':
        filename = d.get('filename', '')
        if filename:
            basename = os.path.basename(filename)
            title = os.path.splitext(basename)[0]
            title_lower = title.lower()
            
            if title_lower in existing_songs:
                logger.info(f"Skipping existing track: {title}")
                clean_partial_downloads(output_dir, basename)
                if progress_callback:
                    progress_callback({'status': 'skipped', 'current_track': title})
                d['status'] = 'skipped'
                return
            
            with downloading_lock:
                if title_lower not in downloading_tracks:
                    downloading_tracks.add(title_lower)
                    logger.info(f"Starting download: {title}")
                    if progress_callback:
                        progress_callback({'status': 'downloading', 'current_track': title})
    
    elif d['status'] == 'finished':
        filename = d.get('filename', '')
        if filename:
            basename = os.path.basename(filename)
            title = os.path.splitext(basename)[0]
            title_lower = title.lower()
            with downloading_lock:
                if title_lower in downloading_tracks:
                    downloading_tracks.remove(title_lower)
                    logger.info(f"Finished downloading: {title}")
                    if progress_callback:
                        progress_callback({'status': 'finished', 'current_track': title})
    
    elif d['status'] == 'error':
        filename = d.get('filename', '')
        if filename:
            basename = os.path.basename(filename)
            title = os.path.splitext(basename)[0]
            clean_partial_downloads(output_dir, basename)
            if progress_callback:
                progress_callback({'status': 'error', 'current_track': title})

def load_archive(output_dir: str) -> Set[str]:
    """
    Load the set of track IDs already downloaded into this directory.
    Lines use yt-dlp's download-archive format: "soundcloud <track id>".
    """
    archive_path = os.path.join(output_dir, ARCHIVE_FILENAME)
    if not os.path.exists(archive_path):
        return set()
    with open(archive_path, encoding='utf-8') as f:
        return {line.strip() for line in f if line.strip()}

def record_in_archive(output_dir: str, track_id: str) -> None:
    """
    Append a track ID to the directory's archive so later runs skip it without an API call.
    """
    with archive_lock:
        with open(os.path.join(output_dir, ARCHIVE_FILENAME), 'a', encoding='utf-8') as f:
            f.write(f"soundcloud {track_id}\n")

def load_playlist_index(base_dir: str) -> Dict[str, Dict[str, Any]]:
    """
    Load the playlist index (URL -> folder name and track count) from the base directory.
    """
    index_path = os.path.join(base_dir, INDEX_FILENAME)
    try:
        with open(index_path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def update_playlist_index(base_dir: str, url: str, folder: str, track_ids: List[str]) -> None:
    """
    Record a playlist's folder and current track IDs after a successful fetch.
    """
    index = load_playlist_index(base_dir)
    index[url] = {'folder': folder, 'total': len(track_ids), 'ids': track_ids}
    index_path = os.path.join(base_dir, INDEX_FILENAME)
    tmp_path = index_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, index_path)

def get_missing_percent(url: str, base_dir: str) -> float:
    """
    Percentage of a playlist's tracks not yet downloaded, using only local state.
    Playlists that have never been fetched count as 100% missing.
    """
    entry = load_playlist_index(base_dir).get(url)
    if not entry or not entry.get('total'):
        return 100.0
    archived = load_archive(os.path.join(base_dir, entry['folder']))
    ids = entry.get('ids')
    if ids:
        # Only count archived tracks that are still in the playlist, so removed or
        # replaced tracks don't make the playlist look more complete than it is
        missing = sum(1 for track_id in ids if f"soundcloud {track_id}" not in archived)
        return 100.0 * missing / len(ids)
    # Index entries written before IDs were stored: approximate until the next fetch
    return max(0.0, 100.0 * (entry['total'] - len(archived)) / entry['total'])

def download_track_by_url(url: str, output_dir: str, ydl_opts: Dict[str, Any]) -> bool:
    """
    Download a single track by URL.
    Returns True if download was successful (or skipped by match_filter), False otherwise.
    """
    import yt_dlp
    try:
        # Raise on errors (instead of ignoreerrors) so 403s can be detected
        with yt_dlp.YoutubeDL({**ydl_opts, 'ignoreerrors': False}) as ydl:
            ydl.download([url])
        note_request_result(got_403=False)
        return True
    except Exception as e:
        note_request_result(got_403=is_rate_limit_error(e))
        # yt-dlp has already logged its own DownloadErrors through our logger
        if not isinstance(e, yt_dlp.utils.DownloadError):
            logger.error(f"Failed to download {url}: {str(e)}")
        return False

def download_single_track(track: Dict[str, str], output_dir: str, existing_songs: Set[str], ydl_opts: Dict[str, Any]) -> bool:
    """
    Download a single track from SoundCloud.
    Returns True if download was successful or track already exists, False on error.

    The track's metadata is only fetched once, by the download itself. If its title
    matches a file already in output_dir, match_filter skips the actual download.
    Either way the track ID is recorded in the archive so future runs skip it for free.
    """
    def skip_if_exists(info: Dict[str, Any], *, incomplete: bool = False) -> Optional[str]:
        title = info.get('title') or ''
        if title.lower() in existing_songs:
            logger.info(f"Skipping existing track: {title}")
            return f"{title} already exists"
        return None

    # Don't send any more requests once SoundCloud has started rate limiting
    if rate_limited.is_set():
        return False

    success = download_track_by_url(track['url'], output_dir, {**ydl_opts, 'match_filter': skip_if_exists})
    if success:
        record_in_archive(output_dir, track['id'])
    return success

def check_ffmpeg() -> None:
    """
    Check if FFmpeg is installed and attempt to install it if not present.
    """
    try:
        # Try to run ffmpeg to check if it's installed
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        logger.info("FFmpeg is already installed")
        return
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.warning("FFmpeg not found. Attempting to install...")
        
        system = platform.system().lower()
        try:
            if system == 'windows':
                # For Windows, guide the user to install FFmpeg manually
                logger.error("FFmpeg installation on Windows requires manual installation:")
                logger.error("1. Download FFmpeg from https://www.gyan.dev/ffmpeg/builds/")
                logger.error("2. Extract the archive")
                logger.error("3. Add the bin folder to your system PATH")
                logger.error("4. Restart your terminal/IDE and try again")
                sys.exit(1)
            elif system == 'darwin':  # macOS
                subprocess.run(['brew', 'install', 'ffmpeg'], check=True)
            elif system == 'linux':
                # Try apt-get first (Debian/Ubuntu)
                try:
                    subprocess.run(['sudo', 'apt-get', 'update'], check=True)
                    subprocess.run(['sudo', 'apt-get', 'install', '-y', 'ffmpeg'], check=True)
                except subprocess.SubprocessError:
                    # Try dnf (Fedora)
                    try:
                        subprocess.run(['sudo', 'dnf', 'install', '-y', 'ffmpeg'], check=True)
                    except subprocess.SubprocessError:
                        # Try pacman (Arch)
                        subprocess.run(['sudo', 'pacman', '-S', '--noconfirm', 'ffmpeg'], check=True)
            
            logger.info("Successfully installed FFmpeg")
        except Exception as e:
            logger.error("Failed to install FFmpeg automatically")
            logger.error("Please install FFmpeg manually for your operating system")
            logger.error("For more information, visit: https://ffmpeg.org/download.html")
            sys.exit(1)

def check_and_install_yt_dlp() -> None:
    """
    Check if yt-dlp is installed and install it if not present.
    On Linux, use apt. On Windows, use pip.
    """
    try:
        import yt_dlp
        logger.info("yt-dlp is already installed")
    except ImportError:
        logger.info("yt-dlp not found. Attempting to install...")
        import platform
        system = platform.system().lower()
        try:
            if system == 'linux':
                logger.info("Attempting to install yt-dlp using apt (Linux)...")
                subprocess.check_call(['sudo', 'apt', 'update'])
                subprocess.check_call(['sudo', 'apt', 'install', '-y', 'yt-dlp'])
            elif system == 'windows':
                logger.info("Attempting to install yt-dlp using pip (Windows)...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
            else:
                logger.error(f"Automatic yt-dlp installation not supported for OS: {system}. Please install yt-dlp manually.")
                sys.exit(1)
            logger.info("Successfully installed yt-dlp")
        except subprocess.CalledProcessError as e:
            logger.error("Failed to install yt-dlp automatically")
            logger.error("Please install it manually using: pip install --upgrade yt-dlp or your system's package manager.")
            sys.exit(1)

def validate_url(url: str) -> bool:
    """
    Validate if the provided URL is a valid SoundCloud URL.
    """
    try:
        parsed = urlparse(url)
        return all([parsed.scheme, parsed.netloc]) and 'soundcloud.com' in parsed.netloc
    except Exception:
        return False

def validate_directory(directory: str) -> str:
    """
    Validate and create the output directory if it doesn't exist.
    Returns the absolute path to the directory.
    """
    abs_path = os.path.abspath(directory)
    if not os.path.exists(abs_path):
        try:
            os.makedirs(abs_path)
            logger.info(f"Created output directory: {abs_path}")
        except Exception as e:
            logger.error(f"Failed to create directory {abs_path}: {str(e)}")
            raise
    return abs_path

def extract_playlist_info(url: str, retries: int = 3, backoff_seconds: float = 5.0) -> Optional[Dict[str, Any]]:
    """
    Fetch playlist metadata (title + flat entries) in a single yt-dlp call,
    retrying with exponential backoff since SoundCloud intermittently
    throttles the playlist-resolve request for large playlists.
    Raises RateLimitedError if every attempt failed with HTTP 403.
    """
    import time
    import yt_dlp

    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
    }

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    return info
                logger.warning(f"Playlist extraction returned no data (attempt {attempt}/{retries}), possibly rate limited")
        except Exception as e:
            last_error = e
            logger.warning(f"Failed to extract playlist info (attempt {attempt}/{retries}): {type(e).__name__}: {str(e)}")
            cause = e.__cause__
            if cause:
                logger.warning(f"Underlying cause: {type(cause).__name__}: {str(cause)}")
            if attempt == retries:
                logger.exception("Full traceback of final extraction failure:")

        if attempt < retries:
            sleep_time = backoff_seconds * (2 ** (attempt - 1))
            logger.info(f"Retrying playlist extraction in {sleep_time:.0f}s...")
            time.sleep(sleep_time)

    logger.error("Failed to extract playlist info after all retries")
    if last_error is not None and is_rate_limit_error(last_error):
        raise RateLimitedError(str(last_error))
    return None

def get_playlist_tracks(info: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Extract individual track IDs and URLs from already-fetched playlist info.
    """
    entries = info.get('entries') or []
    return [{'id': str(entry['id']), 'url': entry['url']}
            for entry in entries if entry.get('url') and entry.get('id')]

def get_playlist_title(info: Dict[str, Any]) -> str:
    """
    Extract the playlist title from already-fetched playlist info.
    Returns the playlist title as a string, or 'playlist' if not found.
    """
    return info.get('title') or 'playlist'

def download_playlist(url: str, output_dir: str, tracks: List[Dict[str, str]], max_concurrent: int = MAX_CONCURRENT_DOWNLOADS, audio_format: str = 'mp3', progress_callback=None) -> None:
    """
    Download a SoundCloud playlist using yt-dlp with concurrent downloads.
    Raises RateLimitedError if downloads were stopped because of repeated 403s.
    """
    import yt_dlp

    # Get existing songs before starting download
    existing_songs = get_existing_songs(output_dir)
    logger.info(f"Found {len(existing_songs)} existing songs in the output directory")

    if not tracks:
        logger.error("No tracks found in playlist or failed to extract track information")
        return

    global probe_url
    probe_url = url

    total_tracks = len(tracks)
    archived = load_archive(output_dir)
    pending = [t for t in tracks if f"soundcloud {t['id']}" not in archived]
    already_done = total_tracks - len(pending)
    logger.info(f"Found {total_tracks} tracks in playlist; {already_done} already downloaded (no API call needed), {len(pending)} to process")
    
    if progress_callback:
        progress_callback({
            'total': total_tracks,
            'downloaded': 0,
            'status': 'starting'
        })
    
    # Configure format based on user preference
    format_config = {
        'mp3': {
            'format': 'bestaudio[ext=mp3]/bestaudio/best',
            'audio_format': 'mp3',
            'audio_quality': '192K',
            'preferredcodec': 'mp3',
        },
        'opus': {
            'format': 'bestaudio[ext=opus]/bestaudio/best',
            'audio_format': 'opus',
            'audio_quality': '192K',
            'preferredcodec': 'opus',
        }
    }[audio_format]
    
    ydl_opts = {
        'format': format_config['format'],
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'ignoreerrors': True,
        'extract_flat': False,
        'quiet': True,
        'no_warnings': True,
        'logger': logger,
        'progress_hooks': [lambda d: download_progress_hook(d, existing_songs, output_dir, progress_callback)],
        'extract_audio': True,
        'audio_format': format_config['audio_format'],
        'audio_quality': format_config['audio_quality'],
        'prefer_ffmpeg': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': format_config['preferredcodec'],
            'preferredquality': '192',
            'nopostoverwrites': False
        }, {
            'key': 'FFmpegMetadata',
            'add_metadata': True,
        }],
        'keepvideo': False,
        'postprocessor_args': [
            '-ar', '44100',
            '-ac', '2',
        ],
        'continue': True,
        'no_abort_on_error': True,
    }
    
    logger.info(f"Starting concurrent downloads with {max_concurrent} workers")
    logger.info(f"Output format: {audio_format}")
    
    downloaded = already_done
    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        future_to_url = {
            executor.submit(download_single_track, track, output_dir, existing_songs, ydl_opts): track['url']
            for track in pending
        }
        
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                if future.result():
                    downloaded += 1
                    if progress_callback:
                        progress_callback({
                            'total': total_tracks,
                            'downloaded': downloaded,
                            'status': 'progress'
                        })
            except Exception as e:
                if "File already exists, skipping..." not in str(e):
                    logger.error(f"Error processing {url}: {str(e)}")
    
    logger.info("All downloads completed")
    logger.info(f"Downloaded {downloaded} out of {total_tracks} tracks.")
    # Final cleanup of any remaining fragments
    clean_partial_downloads(output_dir)

    if rate_limited.is_set():
        raise RateLimitedError(f"Stopped after {RATE_LIMIT_403_THRESHOLD} consecutive HTTP 403s")

def main() -> None:
    """
    Main function to execute the script's primary functionality.
    """
    global MAX_CONCURRENT_DOWNLOADS
    
    try:
        # Set up argument parser
        parser = argparse.ArgumentParser(description='Download SoundCloud playlists')
        parser.add_argument('url', nargs='?', help='SoundCloud playlist URL')
        parser.add_argument('output_dir', nargs='?', help='Output directory for downloaded files')
        parser.add_argument('--threads', type=int, help=f'Number of concurrent downloads (default: {MAX_CONCURRENT_DOWNLOADS})')
        parser.add_argument('--format', choices=['mp3', 'opus'], default='mp3', help='Audio format (default: mp3)')
        parser.add_argument('-d', '--cleanup', action='store_true', help='Clean up partial downloads only')
        parser.add_argument('--missing-percent', action='store_true',
                            help='Print the percentage of the playlist not yet downloaded (local check, no API calls) and exit')
        args = parser.parse_args()

        # Local-only check used by the runner to order playlists; prints just the number
        if args.missing_percent:
            if not args.url or not args.output_dir:
                parser.print_help()
                sys.exit(1)
            print(f"{get_missing_percent(args.url, os.path.abspath(args.output_dir)):.1f}")
            return

        logger.info("Starting script execution")
        
        # Handle cleanup-only mode
        if args.cleanup:
            if args.output_dir:
                clean_partial_downloads(args.output_dir)
            else:
                logger.error("Please specify a directory to clean")
            return
            
        # Validate URL and directory are provided for download mode
        if not args.url or not args.output_dir:
            parser.print_help()
            sys.exit(1)
        
        # Update concurrent downloads if specified
        if args.threads:
            MAX_CONCURRENT_DOWNLOADS = max(1, min(args.threads, 16))  # Cap at 16 threads
            logger.info(f"Using {MAX_CONCURRENT_DOWNLOADS} concurrent downloads")
        
        # Validate inputs
        if not validate_url(args.url):
            logger.error("Invalid SoundCloud URL provided")
            sys.exit(1)
        
        # Fetch playlist metadata once (title + track list) to avoid duplicate requests
        playlist_info = extract_playlist_info(args.url)
        if not playlist_info:
            logger.error("Could not retrieve playlist information (possibly rate limited). Aborting.")
            sys.exit(1)

        playlist_title = get_playlist_title(playlist_info)
        tracks = get_playlist_tracks(playlist_info)
        if not tracks:
            logger.error("No tracks found in playlist or failed to extract track information")
            sys.exit(1)

        safe_title = playlist_title.replace(os.sep, '_').replace(' ', '_')
        base_dir = validate_directory(args.output_dir)
        output_dir = validate_directory(os.path.join(base_dir, safe_title))
        update_playlist_index(base_dir, args.url, safe_title, [t['id'] for t in tracks])

        # Check for dependencies
        check_ffmpeg()
        check_and_install_yt_dlp()

        # Download the playlist
        download_playlist(args.url, output_dir, tracks, MAX_CONCURRENT_DOWNLOADS, args.format)

    except RateLimitedError as e:
        logger.error(f"Rate limited by SoundCloud ({e}). Run again later to continue where this left off.")
        sys.exit(RATE_LIMITED_EXIT_CODE)
    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
