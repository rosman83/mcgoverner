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
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

[ -d .venv ] || uv venv .venv --quiet
uv pip install --python .venv/bin/python --quiet -r requirements.txt

if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "WARNING: no LLM key set. Add DEEPSEEK_API_KEY or OPENROUTER_API_KEY to .env."
  echo "         Pick the provider with LLM_PROVIDER=deepseek|openrouter (default: whichever key is set)."
fi

exec .venv/bin/uvicorn app.main:app --reload --port 8000
