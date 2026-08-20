#!/bin/bash
# The Mac launcher's actual logic. Wrapped into a double-clickable .app by
# build_app.sh (osacompile) — see packaging/README.md.
#
# Job: get the latest code onto the machine, then hand off to run.sh, which
# owns dependency install and actually starting the server. Re-running this
# (i.e. relaunching the app) is the auto-update: it re-pulls REPO_BRANCH
# every time.
set -uo pipefail

REPO_URL="https://github.com/rosman83/mcgoverner.git"
REPO_BRANCH="main"
ZIP_URL="https://github.com/rosman83/mcgoverner/archive/refs/heads/${REPO_BRANCH}.zip"
RUN_LOG="/tmp/mcgoverner-run.log"

# Every failure path used to just... end. No window, no error, nothing visible
# for someone who doesn't know to go looking for a log file in /tmp. Show an
# actual dialog with enough of the real error to screenshot and send.
show_error() {
  local msg="$1"
  local detail
  detail="$(tail -c 1500 "$RUN_LOG" 2>/dev/null)"
  osascript - "$msg" "$detail" <<'APPLESCRIPT' >/dev/null 2>&1
on run argv
  set theMsg to item 1 of argv
  set theDetail to item 2 of argv
  display dialog theMsg & return & return & theDetail buttons {"OK"} ¬
    with title "McGoverner failed to start" with icon stop
end run
APPLESCRIPT
}

# macOS ships no `timeout`/`gtimeout` by default. A stalled network call (a
# proxy that hangs the connection instead of rejecting it, rather than an
# outright block) used to hang this forever with the app just sitting open,
# doing nothing, no error - worse than a clean failure.
with_timeout() {
  local secs="$1"; shift
  "$@" &
  local cmd_pid=$!
  ( sleep "$secs" && kill -TERM "$cmd_pid" 2>/dev/null ) &
  local watcher_pid=$!
  disown "$watcher_pid" 2>/dev/null   # otherwise bash logs "Terminated: 15" when it's killed below
  wait "$cmd_pid" 2>/dev/null
  local status=$?
  kill "$watcher_pid" 2>/dev/null
  return $status
}

# Fetch the code with plain curl + unzip, no git required. Most people have
# neither git nor Xcode Command Line Tools installed - the first time `git`
# runs on a machine without them, macOS pops up its own "install developer
# tools" prompt and waits on it, which most people don't know to click through
# (this is what was actually hanging - see the fetch_code comment below).
# curl/unzip/rsync ship with every Mac; git does not.
fetch_code() {
  local tmp src
  tmp="$(mktemp -d)" || return 1
  if ! curl -sL --connect-timeout 15 --max-time 60 -o "$tmp/repo.zip" "$ZIP_URL" >> "$RUN_LOG" 2>&1; then
    rm -rf "$tmp"; return 1
  fi
  if ! unzip -q "$tmp/repo.zip" -d "$tmp/extracted" >> "$RUN_LOG" 2>&1; then
    rm -rf "$tmp"; return 1
  fi
  src="$(find "$tmp/extracted" -mindepth 1 -maxdepth 1 -type d | head -1)"
  if [ -z "$src" ]; then
    rm -rf "$tmp"; return 1
  fi
  mkdir -p "$INSTALL_DIR"
  # Overlay the code onto the persistent install dir. .env/data/lectures/.venv
  # are excluded from both the copy AND the delete pass, so a fresh download
  # every launch never touches config or imported lectures - this is simpler
  # than diffing against git history and works identically for every user.
  rsync -a --delete \
    --exclude=.env --exclude=data --exclude=lectures --exclude=.venv --exclude=.git \
    "$src"/ "$INSTALL_DIR"/ >> "$RUN_LOG" 2>&1
  rm -rf "$tmp"

  # Stamp the actual commit hash just downloaded into a .version file, so
  # main.py can show something more useful than "dev" (git rev-parse always
  # fails here - this zip download has no .git). Best-effort: written after
  # rsync's mirror pass so it's never itself deleted by --delete, and a
  # failure here (offline, GitHub rate limit, unexpected API response shape)
  # just leaves the app showing "dev" - nothing else depends on this
  # succeeding. No jq/python dependency - grep+sed only, since this step runs
  # before this script has set up anything else on the machine.
  local sha
  sha="$(curl -sL --max-time 10 -H "User-Agent: McGoverner-Launcher" \
    "https://api.github.com/repos/rosman83/mcgoverner/commits/${REPO_BRANCH}" 2>/dev/null \
    | grep -m1 '"sha"' | sed -E 's/.*"sha": *"([^"]+)".*/\1/' | cut -c1-7)"
  if [ -n "$sha" ]; then
    echo "$sha" > "$INSTALL_DIR/.version"
  fi
}

# If this app is running from inside an existing checkout of this repo (a local
# dev build via build_app.sh, still sitting in packaging/), use that checkout in
# place via git instead — git is guaranteed to already be there (it's how the
# checkout exists at all), and this preserves normal `git pull` semantics for
# active development. A distributed, downloaded copy (what real end users get,
# sitting in ~/Downloads with no surrounding repo) won't match anything here.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/McGoverner"
IS_DEV_CHECKOUT=0
dir="$SCRIPT_DIR"
for _ in 1 2 3 4 5 6; do
  dir="$(dirname "$dir")"
  if [ -d "$dir/.git" ] && git -C "$dir" remote get-url origin 2>/dev/null | grep -qi "mcgoverner"; then
    INSTALL_DIR="$dir"
    IS_DEV_CHECKOUT=1
    echo "Running from a local checkout ($INSTALL_DIR) — using it in place."
    break
  fi
done

if [ "$IS_DEV_CHECKOUT" -eq 1 ]; then
  # http.lowSpeedLimit/-Time aborts a transfer that stalls mid-flight;
  # with_timeout is the backstop for a connection that never gets that far.
  GIT="git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=15"
  if ! with_timeout 30 $GIT -C "$INSTALL_DIR" fetch --quiet origin "$REPO_BRANCH" > "$RUN_LOG" 2>&1 \
      || ! $GIT -C "$INSTALL_DIR" checkout --quiet "$REPO_BRANCH" >> "$RUN_LOG" 2>&1 \
      || ! $GIT -C "$INSTALL_DIR" pull --quiet --ff-only origin "$REPO_BRANCH" >> "$RUN_LOG" 2>&1; then
    echo "Update check failed (offline, VPN/firewall, or local edits in $INSTALL_DIR) — launching the version already installed." >> "$RUN_LOG"
  fi
else
  if ! fetch_code; then
    show_error "Could not download McGoverner. Check your internet connection — a corporate VPN or firewall blocking/intercepting github.com will also cause this."
    exit 1
  fi
fi

cd "$INSTALL_DIR"
chmod +x run.sh

# Reap a server left running from a launcher that crashed, was force-quit, or
# lost its Mac to sleep before it reached its own cleanup code - otherwise
# port 8000 stays squatted and this launch either fails to bind or silently
# ends up talking to the old, now out-of-date server while reporting
# success. Only touches a process whose command line points at this install
# dir, so it can't kill an unrelated app that happens to also use port 8000.
for pid in $(lsof -ti:8000 2>/dev/null); do
  if ps -o command= -p "$pid" 2>/dev/null | grep -qF "$INSTALL_DIR"; then
    kill "$pid" 2>/dev/null
  fi
done

./run.sh > "$RUN_LOG" 2>&1 &
SERVER_PID=$!

# Open the browser once the server actually answers, not on a blind timer —
# uv installing dependencies on a first run can take a while. Bail early if
# the server process has already died instead of polling the full timeout.
started=0
for _ in $(seq 1 120); do
  if curl -s -o /dev/null http://localhost:8000; then
    open http://localhost:8000
    started=1
    break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || break
  sleep 1
done

if [ "$started" -eq 0 ]; then
  show_error "McGoverner didn't start. This is usually a dependency that failed to install, or something blocking github.com / astral.sh (corporate VPN or firewall are common causes)."
  # Don't leave a hung install/server stuck running in the background after
  # telling the user it failed - kill it (and its direct children) so the app
  # actually quits instead of "staying open" indefinitely with nothing to show.
  kill "$SERVER_PID" 2>/dev/null
  pkill -P "$SERVER_PID" 2>/dev/null
  exit 1
fi

echo ""
echo "Ready. Your browser should have opened to McGoverner."
echo "If it didn't, go to: http://localhost:8000"
echo ""
echo "This window must stay open while you use McGoverner - closing it stops the app."
wait "$SERVER_PID" 2>/dev/null
