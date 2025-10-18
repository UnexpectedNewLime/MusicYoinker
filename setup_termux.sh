#!/usr/bin/env bash

# Setup helper for Termux: moves the runner into ~/.shortcuts, makes it executable,
# adds environment variables to ~/.bashrc (if missing) and installs python/yt-dlp via pkg if needed.

set -euo pipefail

SHORTCUTS_DIR="$HOME/.shortcuts"
RUNNER_NAME="download_soundcloud.playlists.sh"
RUNNER_SRC="${RUNNER_SRC:-./$RUNNER_NAME}"
BASHRC="$HOME/.bashrc"

# Environment variables to add
read -r -d '' ENV_VARS <<'EOF'
# MusicYoinker environment variables
export PLAYLISTS_FILE="$HOME/playlists.txt"
export SOUNDCLOUD_BASE_DIR="$HOME/Music"
export SOUNDCLOUD_LOG_DIR="$HOME/logs"
export PY_DOWNLOADER="$HOME/MusicYoinker/SoundCloud_Downloader.py"
EOF

echo "--- MusicYoinker Termux setup ---"

# Ensure runner exists
if [[ ! -f "$RUNNER_SRC" ]]; then
  echo "Runner script not found at: $RUNNER_SRC" >&2
  echo "Please run this script from the repository root or set RUNNER_SRC to the runner path." >&2
  exit 1
fi

# Create shortcuts dir and move runner
mkdir -p "$SHORTCUTS_DIR"
mv -f "$RUNNER_SRC" "$SHORTCUTS_DIR/"
chmod +x "$SHORTCUTS_DIR/$RUNNER_NAME"

echo "Moved $RUNNER_SRC -> $SHORTCUTS_DIR/$RUNNER_NAME and marked executable"

# Append environment variables to ~/.bashrc if they are not already present
if ! grep -qxF '# MusicYoinker environment variables' "$BASHRC" 2>/dev/null; then
  echo "" >> "$BASHRC"
  echo "# MusicYoinker environment variables" >> "$BASHRC"
  echo "export PLAYLISTS_FILE=\"$HOME/playlists.txt\"" >> "$BASHRC"
  echo "export SOUNDCLOUD_BASE_DIR=\"$HOME/Music\"" >> "$BASHRC"
  echo "export SOUNDCLOUD_LOG_DIR=\"$HOME/logs\"" >> "$BASHRC"
  echo "export PY_DOWNLOADER=\"$HOME/MusicYoinker/SoundCloud_Downloader.py\"" >> "$BASHRC"
  echo "Added env vars to $BASHRC"
else
  echo "Environment variables already present in $BASHRC; skipping append"
fi

# Ensure directories exist
mkdir -p "$HOME/Music" "$HOME/logs"

# Install python and yt-dlp if missing (Termux pkg)
install_pkg_if_missing() {
  local cmd="$1" pkgname="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "$cmd is already installed"
  else
    echo "$cmd not found. Installing $pkgname via pkg..."
    if command -v pkg >/dev/null 2>&1; then
      pkg update -y || true
      pkg install -y "$pkgname"
    else
      echo "pkg not found. Are you running in Termux? Please install $pkgname manually." >&2
      return 1
    fi
  fi
}

# Try to install python3; prefer 'python' package name on Termux
if ! install_pkg_if_missing python3 python; then
  # If attempt with pkg package name 'python' above failed to install python3 binary,
  # try installing package named 'python'
  install_pkg_if_missing python python || true
fi

# Install yt-dlp if missing
install_pkg_if_missing yt-dlp yt-dlp || true

echo "Setup complete. Open a new Termux session or run: source $BASHRC"
echo "You can now run the shortcut from the Termux widget or execute: $SHORTCUTS_DIR/$RUNNER_NAME"
