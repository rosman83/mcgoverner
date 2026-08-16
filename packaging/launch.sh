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
REPO_BRANCH="main"   # point this at a different branch to track it instead
INSTALL_DIR="$HOME/Block1Exam"

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
