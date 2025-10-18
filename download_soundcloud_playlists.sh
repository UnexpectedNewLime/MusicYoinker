#!/usr/bin/env bash

source $HOME/.bashrc

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  echo "Usage: $0 [BASE_DIR] [LOG_DIR]"
  echo "  BASE_DIR: Output directory for downloads (default: \$HOME/Music/SoundCloud or SOUNDCLOUD_BASE_DIR)"
  echo "  LOG_DIR:  Directory for logs (default: \$HOME/Logs or SOUNDCLOUD_LOG_DIR)"
  exit 0
fi

# Location of playlists file (one URL per non-empty, non-comment line).
# This is required: set via env var PLAYLISTS_FILE or place a playlists.txt in the current directory.
PLAYLISTS_FILE="${PLAYLISTS_FILE:-playlists.txt}"

# Base output directory
BASE_DIR="${SOUNDCLOUD_BASE_DIR:-${1:-$HOME/music}}"
# Base log directory
LOG_DIR="${SOUNDCLOUD_LOG_DIR:-${2:-$HOME/logs}}"
mkdir -p "$BASE_DIR"
mkdir -p "$LOG_DIR"
echo "BASE_DIR: $BASE_DIR"
echo "LOG_DIR: $LOG_DIR"

if [[ ! -f "$PLAYLISTS_FILE" ]]; then
  echo "Playlists file not found: $PLAYLISTS_FILE" >&2
  echo "Set PLAYLISTS_FILE env var or create $PLAYLISTS_FILE with playlist URLs (one per line)." >&2
  exit 2
fi

urls_to_process=()
while IFS= read -r line || [[ -n "$line" ]]; do
  # Trim leading/trailing whitespace
  url=$(echo "$line" | sed -e 's/^\s*//' -e 's/\s*$//')
  # Skip blank lines and comments
  [[ -z "$url" || ${url:0:1} == "#" ]] && continue
  urls_to_process+=("$url")
done < "$PLAYLISTS_FILE"

for url in "${urls_to_process[@]}"; do
  # Extract playlist name from URL (after last slash)
  playlist_name=$(basename "$url")
  # Output directory for this playlist
  # Log file with date in YY_MM_DD_HHMM format
  log_file="$LOG_DIR/${playlist_name}_$(date +%y_%m_%d_%H%M).log"
  echo "Downloading $url to $BASE_DIR, logging to $log_file"
  # Run the downloader with less threads to avoid getting rate limited, filter unwanted warning, and log output
{ python3 -u /data/data/com.termux/files/home/LimeScripts/Scripts/media/soundcloud/soundcloud_downloader.py  --threads 5 \
    "$url" \
    "$BASE_DIR" \
    2> >(stdbuf -oL grep -v "Unable to download JSON metadata" >&2) ; } &> "$log_file"

  # Per-playlist log retention: keep only the 3 most recent logs for this playlist
  shopt -s nullglob
  mapfile -t logs < <(ls -1t "$LOG_DIR/${playlist_name}_"*.log 2>/dev/null || true)
  if (( ${#logs[@]} > 3 )); then
    to_remove=( "${logs[@]:3}" )
    printf '%s\n' "${to_remove[@]}" | xargs -r rm --
  fi
done

wait
