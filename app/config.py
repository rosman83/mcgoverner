"""Web-editable app configuration, backed by the .env file.

Existing users keep using .env directly - dotenv already loads it into os.environ
before this module is imported, so their values just show up here unchanged. This
gives everyone else a way to set the same variables from the browser instead of a
text file: saving reads the current .env (if any), updates only the given keys,
writes it back preserving untouched lines/comments, and applies the change to the
running process immediately so no restart is needed.
"""
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")

# At least one of these must be set for the app to generate anything.
REQUIRED_ANY = ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY")

# (env var, label, is_secret, help text) - single source of truth for what the
# config page shows/edits and what save_config() is allowed to touch.
FIELDS = [
    ("LLM_PROVIDER", "Text provider", False,
     '"deepseek" or "openrouter". Leave blank to auto-pick whichever key below is set.'),
    ("DEEPSEEK_API_KEY", "DeepSeek API key", True,
     "Billed direct at api.deepseek.com. Recommended for text generation."),
    ("OPENROUTER_API_KEY", "OpenRouter API key", True,
     "Powers the image-description fallback for image-only slides, and works as an "
     "alternate text provider. Get one at openrouter.ai/keys."),
    ("LLM_MODEL", "Model override", False, "Optional - leave blank for the provider's default."),
    ("OPENROUTER_VISION_MODEL", "Vision model override", False,
     "Optional - leave blank for the default (google/gemini-3.7-flash)."),
]
FIELD_NAMES = {name for name, *_ in FIELDS}


def is_configured():
    return any(os.environ.get(k) for k in REQUIRED_ANY)


def _mask(value):
    if not value:
        return ""
    return value[:6] + "…" + value[-4:] if len(value) > 12 else "•" * len(value)


def current_config():
    """Field metadata + masked current values, for the settings page."""
    fields = [
        {
            "name": name,
            "label": label,
            "secret": secret,
            "help": help_text,
            "set": bool(os.environ.get(name)),
            "display": _mask(os.environ.get(name, "")) if secret else os.environ.get(name, ""),
        }
        for name, label, secret, help_text in FIELDS
    ]
    return {"configured": is_configured(), "fields": fields}


def _read_env_lines():
    if not os.path.exists(ENV_PATH):
        return []
    with open(ENV_PATH) as f:
        return f.read().splitlines()


def save_config(updates):
    """Apply `updates` (name -> value; "" clears that key) to .env and the live
    process. Unknown keys are ignored. Existing lines/comments/ordering in .env
    are preserved; new keys are appended."""
    updates = {k: (v or "").strip() for k, v in updates.items() if k in FIELD_NAMES}

    seen = set()
    out = []
    for line in _read_env_lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            seen.add(key)
            if updates[key]:
                out.append(f"{key}={updates[key]}")
            # blank clears it - line dropped entirely
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen and value:
            out.append(f"{key}={value}")

    with open(ENV_PATH, "w") as f:
        f.write("\n".join(out) + ("\n" if out else ""))

    for key, value in updates.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)

    return current_config()
