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
RUN_LOG="/tmp/block1exam-run.log"

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
    with title "Block1Exam failed to start" with icon stop
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

# http.lowSpeedLimit/-Time aborts a transfer that stalls mid-flight; with_timeout
# below is the backstop for a connection that never gets that far at all.
GIT="git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=15"

if [ -d "$INSTALL_DIR/.git" ]; then
  if ! with_timeout 30 $GIT -C "$INSTALL_DIR" fetch --quiet origin "$REPO_BRANCH" > "$RUN_LOG" 2>&1 \
      || ! $GIT -C "$INSTALL_DIR" checkout --quiet "$REPO_BRANCH" >> "$RUN_LOG" 2>&1 \
      || ! $GIT -C "$INSTALL_DIR" pull --quiet --ff-only origin "$REPO_BRANCH" >> "$RUN_LOG" 2>&1; then
    echo "Update check failed (offline, VPN/firewall, or local edits in $INSTALL_DIR) — launching the version already installed." >> "$RUN_LOG"
  fi
else
  if ! with_timeout 60 $GIT clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR" > "$RUN_LOG" 2>&1; then
    show_error "Could not download Block1Exam. Check your internet connection — a corporate VPN or firewall blocking/intercepting github.com will also cause this."
    exit 1
  fi
fi

cd "$INSTALL_DIR"
chmod +x run.sh
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
  show_error "Block1Exam didn't start. This is usually a dependency that failed to install, or something blocking github.com / astral.sh (corporate VPN or firewall are common causes)."
  # Don't leave a hung install/server stuck running in the background after
  # telling the user it failed - kill it (and its direct children) so the app
  # actually quits instead of "staying open" indefinitely with nothing to show.
  kill "$SERVER_PID" 2>/dev/null
  pkill -P "$SERVER_PID" 2>/dev/null
  exit 1
fi

wait "$SERVER_PID" 2>/dev/null
