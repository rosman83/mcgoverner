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

[ -d .venv ] || uv venv .venv --quiet
uv pip install --python .venv/bin/python --quiet -r requirements.txt

if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "WARNING: no LLM key set. Add DEEPSEEK_API_KEY or OPENROUTER_API_KEY to .env."
  echo "         Pick the provider with LLM_PROVIDER=deepseek|openrouter (default: whichever key is set)."
fi

exec .venv/bin/uvicorn app.main:app --reload --port 8000
