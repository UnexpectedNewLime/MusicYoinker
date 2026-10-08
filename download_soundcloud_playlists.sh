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
# Delay between playlists (seconds) to avoid bursting SoundCloud with back-to-back requests
PLAYLIST_DELAY_SECONDS="${SOUNDCLOUD_PLAYLIST_DELAY:-60}"
# When SoundCloud rate limits us, wait this long (seconds) and resume. Blocks observed
# so far cleared within ~5 minutes; tested 5-minute waits resumed cleanly with no 403s.
RATE_LIMIT_WAIT_SECONDS="${SOUNDCLOUD_RATE_LIMIT_WAIT:-300}"
# Give up after this many rate-limit waits in a row that downloaded nothing new, so a block
# that won't lift can't loop forever (rounds that make progress reset the count)
MAX_IDLE_WAITS="${SOUNDCLOUD_MAX_IDLE_WAITS:-3}"

# Total tracks recorded across all playlist archives, used to tell whether a round made progress
count_downloaded() {
  # /dev/null keeps cat from reading stdin when nullglob leaves no matches
  cat /dev/null "$BASE_DIR"/*/.downloaded_ids 2>/dev/null | wc -l
}
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

# Order playlists by % of tracks still missing (highest first) so the most incomplete
# ones get served before any rate limit kicks in. This is a local check: no API calls.
sorted=$(
  for url in "${urls_to_process[@]}"; do
    pct=$(python3 "$PY_DOWNLOADER" --missing-percent "$url" "$BASE_DIR" 2>/dev/null)
    printf '%s\t%s\n' "${pct:-100.0}" "$url"
  done | LC_ALL=C sort -s -t $'\t' -k1,1nr
)
echo "Playlist order (most missing first):"
awk -F'\t' '{printf "  %5s%% missing  %s\n", $1, $2}' <<< "$sorted"
mapfile -t urls_to_process < <(cut -f2 <<< "$sorted")

# Keep the phone awake on Termux, since rate-limit waits can stretch a run to 30+ minutes
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock
  trap 'termux-wake-unlock' EXIT
fi

rate_limit_waits=0
idle_waits=0
i=0
while (( i < ${#urls_to_process[@]} )); do
  url="${urls_to_process[$i]}"
  # Extract playlist name from URL (after last slash)
  playlist_name=$(basename "$url")
  # Output directory for this playlist
  # Log file with date in YY_MM_DD_HHMM format
  log_file="$LOG_DIR/${playlist_name}_$(date +%y_%m_%d_%H%M).log"
  echo "Downloading $url to $BASE_DIR, logging to $log_file"
  downloaded_before=$(count_downloaded)
  # Run the downloader with less threads to avoid getting rate limited, filter unwanted warning, and log output
{ python3 -u $PY_DOWNLOADER  --threads 5 \
    "$url" \
    "$BASE_DIR" \
    2> >(stdbuf -oL grep -v "WARNING.*Unable to download JSON metadata" >&2) ; } &> "$log_file"
  exit_code=$?

  # Per-playlist log retention: keep only the 3 most recent logs for this playlist
  shopt -s nullglob
  mapfile -t logs < <(ls -1t "$LOG_DIR/${playlist_name}_"*.log 2>/dev/null || true)
  if (( ${#logs[@]} > 3 )); then
    to_remove=( "${logs[@]:3}" )
    printf '%s\n' "${to_remove[@]}" | xargs -r rm --
  fi

  # Exit code 3 = SoundCloud is rate limiting (repeated HTTP 403s). Moving on would just
  # extend the block, so wait it out and retry this playlist; downloaded tracks are archived
  # so the retry only fetches what's still missing.
  # Any round that adds tracks resets the idle count, whether or not it hit the rate limit
  new_tracks=$(( $(count_downloaded) - downloaded_before ))
  if (( new_tracks > 0 )); then
    idle_waits=0
  fi

  if (( exit_code == 3 )); then
    if (( new_tracks == 0 )); then
      idle_waits=$((idle_waits + 1))
    fi
    if (( idle_waits >= MAX_IDLE_WAITS )); then
      echo "Still rate limited after $idle_waits waits with no new downloads. Stopping; run again later to continue."
      break
    fi
    rate_limit_waits=$((rate_limit_waits + 1))
    echo "Rate limited by SoundCloud while processing $url after $new_tracks new tracks. Waiting ${RATE_LIMIT_WAIT_SECONDS}s before resuming (wait #$rate_limit_waits, $idle_waits/$MAX_IDLE_WAITS idle)..."
    sleep "$RATE_LIMIT_WAIT_SECONDS"
    continue
  fi

  i=$((i + 1))
  if (( i < ${#urls_to_process[@]} )); then
    echo "Waiting ${PLAYLIST_DELAY_SECONDS}s before next playlist to avoid rate limiting..."
    sleep "$PLAYLIST_DELAY_SECONDS"
  fi
done

wait
