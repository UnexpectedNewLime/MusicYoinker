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
import logging
import subprocess
import argparse
import platform
from typing import Optional, List, Set, Dict, Any, Tuple
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
from threading import Lock

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

def get_track_info(url: str) -> Optional[Dict[str, Any]]:
    """
    Get track information without downloading.
    Returns None if track info cannot be retrieved.
    """
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info if info else None
    except Exception as e:
        logger.error(f"Failed to get track info: {str(e)}")
        return None

def should_skip_track(title: str, existing_songs: Set[str]) -> bool:
    """
    Check if a track should be skipped based on existing songs.
    """
    return title.lower() in existing_songs

def download_track_by_url(url: str, output_dir: str, ydl_opts: Dict[str, Any]) -> bool:
    """
    Download a single track by URL.
    Returns True if download was successful, False otherwise.
    """
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        if not str(e).endswith('skipped'):
            logger.error(f"Failed to download {url}: {str(e)}")
        return False

def download_single_track(url: str, output_dir: str, existing_songs: Set[str], ydl_opts: Dict[str, Any]) -> bool:
    """
    Download a single track from SoundCloud.
    Returns True if download was successful or track already exists, False on error.
    
    Can be used independently for single track downloads:
    ```python
    # Example usage for single track:
    url = "https://soundcloud.com/user/track"
    output_dir = "./downloads"
    existing_songs = get_existing_songs(output_dir)
    ydl_opts = get_yt_dlp_options(output_dir)  # Define this function with your preferred options
    success = download_single_track(url, output_dir, existing_songs, ydl_opts)
    ```
    """
    # Get track info first
    info = get_track_info(url)
    if not info:
        return False
        
    title = info.get('title', '')
    if not title:
        logger.error("Could not get track title")
        return False
        
    # Check if should skip
    if should_skip_track(title, existing_songs):
        logger.info(f"Skipping existing track: {title}")
        return True
        
    # Download the track
    success = download_track_by_url(url, output_dir, ydl_opts)
    
    # Clean up on failure
    if not success:
        try:
            clean_partial_downloads(output_dir, f"{title}.mp3")
        except Exception as e:
            logger.debug(f"Failed to clean up fragments: {str(e)}")
    
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

def get_playlist_tracks(url: str) -> List[str]:
    """
    Extract individual track URLs from a playlist.
    """
    import yt_dlp
    
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                return [entry['url'] for entry in info['entries'] if entry.get('url')]
            return []
        except Exception as e:
            logger.error(f"Failed to extract playlist info: {str(e)}")
            return []

def get_playlist_title(url: str) -> str:
    """
    Extract the playlist title from the SoundCloud URL using yt-dlp.
    Returns the playlist title as a string, or 'playlist' if not found.
    """
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('title', 'playlist')
    except Exception as e:
        logger.error(f"Failed to get playlist title: {str(e)}")
        return 'playlist'

def download_playlist(url: str, output_dir: str, max_concurrent: int = MAX_CONCURRENT_DOWNLOADS, audio_format: str = 'mp3', progress_callback=None) -> None:
    """
    Download a SoundCloud playlist using yt-dlp with concurrent downloads.
    """
    import yt_dlp
    
    # Get existing songs before starting download
    existing_songs = get_existing_songs(output_dir)
    logger.info(f"Found {len(existing_songs)} existing songs in the output directory")
    
    # Get all track URLs from the playlist
    track_urls = get_playlist_tracks(url)
    if not track_urls:
        logger.error("No tracks found in playlist or failed to extract track information")
        return
    
    total_tracks = len(track_urls)
    logger.info(f"Found {total_tracks} tracks in playlist")
    
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
    
    downloaded = 0
    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        future_to_url = {
            executor.submit(download_single_track, url, output_dir, existing_songs, ydl_opts): url
            for url in track_urls
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
        args = parser.parse_args()

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
        
        # Get playlist title and create subfolder
        playlist_title = get_playlist_title(args.url)
        safe_title = playlist_title.replace(os.sep, '_').replace(' ', '_')
        output_dir = os.path.join(validate_directory(args.output_dir), safe_title)
        output_dir = validate_directory(output_dir)
        
        # Check for dependencies
        check_ffmpeg()
        check_and_install_yt_dlp()
        
        # Download the playlist
        download_playlist(args.url, output_dir, MAX_CONCURRENT_DOWNLOADS, args.format)
        
    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
