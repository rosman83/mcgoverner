#!/bin/bash
cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
if [ ! -f .venv/bin/uvicorn ]; then
  python3 -m venv .venv
  .venv/bin/pip install --quiet fastapi "uvicorn[standard]" PyMuPDF python-pptx openai python-multipart python-dotenv
fi
if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "WARNING: DEEPSEEK_API_KEY not set. Summaries/question generation will fail until you export it."
fi
exec .venv/bin/uvicorn app.main:app --reload --port 8000
