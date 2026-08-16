#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

mkdir -p data lectures

# uv is what the Mac launcher bootstraps too — this is the one place install/venv
# logic lives, so `./run.sh` and the double-click launcher can't drift apart.
# A stalled connection (VPN/proxy that hangs rather than rejects) used to make
# this sit forever with no error - bound every network call here so a bad
# network fails fast and loud instead of hanging silently.
export PATH="$HOME/.local/bin:$PATH"
export UV_HTTP_TIMEOUT=60
if ! command -v uv >/dev/null 2>&1; then
  curl --connect-timeout 15 --max-time 60 -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Pin the interpreter explicitly instead of letting uv pick whatever's already
# on PATH. The code uses `str | None`-style unions (needs 3.10+), and macOS's
# own /usr/bin/python3 stub is stuck on 3.9.6 - it never gets updated, and a
# python.org install doesn't help either, since the launcher runs in a
# non-login shell that never sourced the profile that put it on PATH. `--python
# 3.12` makes uv use its own managed interpreter regardless of any of that.
# Also rebuild if an old run already created a venv against the wrong one.
MIN_PY_MINOR=10
venv_python_ok() {
  [ -x .venv/bin/python ] || return 1
  minor="$(.venv/bin/python -c 'import sys; print(sys.version_info[1])' 2>/dev/null)" || return 1
  [ "$minor" -ge "$MIN_PY_MINOR" ]
}
if [ -d .venv ] && ! venv_python_ok; then
  echo "Existing .venv is on too old a Python — rebuilding it."
  rm -rf .venv
fi
[ -d .venv ] || uv venv .venv --python 3.12 --quiet
uv pip install --python .venv/bin/python --quiet -r requirements.txt

if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "WARNING: no LLM key set. Add DEEPSEEK_API_KEY or OPENROUTER_API_KEY to .env."
  echo "         Pick the provider with LLM_PROVIDER=deepseek|openrouter (default: whichever key is set)."
fi

exec .venv/bin/uvicorn app.main:app --reload --port 8000
