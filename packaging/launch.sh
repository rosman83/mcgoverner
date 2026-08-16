#!/bin/bash
# The Mac launcher's actual logic. Wrapped into a double-clickable .app by
# build_app.sh (osacompile) — see packaging/README.md.
#
# Job: get the latest code onto the machine, then hand off to run.sh, which
# owns dependency install and actually starting the server. Re-running this
# (i.e. relaunching the app) is the auto-update: it re-pulls REPO_BRANCH
# every time.
set -uo pipefail

REPO_URL="https://github.com/redfluff20/Block1Exam.git"
REPO_BRANCH="rashid/vision-fallback-and-config"   # everyone tracks this branch, not main

# If this app is running from inside an existing checkout of this repo (a local
# dev build via build_app.sh, still sitting in packaging/), use that checkout in
# place instead of a separate ~/Block1Exam clone — its .env and data/ are real,
# a fresh clone elsewhere would never see them. A distributed, downloaded copy
# (what real end users get, sitting in ~/Downloads with no surrounding repo)
# won't match anything here and falls through to the normal clone below.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/Block1Exam"
dir="$SCRIPT_DIR"
for _ in 1 2 3 4 5 6; do
  dir="$(dirname "$dir")"
  if [ -d "$dir/.git" ] && git -C "$dir" remote get-url origin 2>/dev/null | grep -q "Block1Exam"; then
    INSTALL_DIR="$dir"
    echo "Running from a local checkout ($INSTALL_DIR) — using it in place."
    break
  fi
done

if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" fetch --quiet origin "$REPO_BRANCH"
  git -C "$INSTALL_DIR" checkout --quiet "$REPO_BRANCH"
  git -C "$INSTALL_DIR" pull --quiet --ff-only origin "$REPO_BRANCH" \
    || echo "Update check failed (offline, or local edits in $INSTALL_DIR) — launching the version already installed."
else
  git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR" \
    || { echo "Could not clone $REPO_URL — check your internet connection."; exit 1; }
fi

cd "$INSTALL_DIR"
chmod +x run.sh
./run.sh &
SERVER_PID=$!

# Open the browser once the server actually answers, not on a blind timer —
# uv installing dependencies on a first run can take a while.
for _ in $(seq 1 120); do
  if curl -s -o /dev/null http://localhost:8000; then
    open http://localhost:8000
    break
  fi
  sleep 1
done

wait "$SERVER_PID"
