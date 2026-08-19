"""Web-editable app configuration, backed by the .env file.

Existing users keep using .env directly - dotenv already loads it into os.environ
before this module is imported, so their values just show up here unchanged. This
gives everyone else a way to set the same variables from the browser instead of a
text file: saving reads the current .env (if any), updates only the given keys,
writes it back preserving untouched lines/comments, and applies the change to the
running process immediately so no restart is needed.

Everyone defaults to OpenRouter for everything (text + vision, one fixed model -
see app/llm/client.py). DeepSeek is an advanced, opt-in, text-only override for
people who already have DeepSeek credit to use up.
"""
import os
import json
import urllib.request
import urllib.error

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")

# OpenRouter is always required - it's the default for everything, and vision
# always uses it even when DeepSeek is handling text.
REQUIRED = ("OPENROUTER_API_KEY",)

TRUE_STRINGS = ("1", "true", "yes")

# (env var, label, type, is_advanced, help text) - single source of truth for
# what the config page shows/edits and what save_config() is allowed to touch.
# type is "secret" (masked text), "text", or "bool" (checkbox).
FIELDS = [
    ("OPENROUTER_API_KEY", "OpenRouter API key", "secret", False,
     "Required. Powers everything - questions, summaries, captions, and image "
     "descriptions. Get one at openrouter.ai/keys."),
    ("USE_DEEPSEEK_FOR_TEXT", "Use DeepSeek for text generation", "bool", True,
     "Advanced. Uses your DeepSeek credit for questions/summaries/captions instead "
     "of OpenRouter. Image descriptions always use OpenRouter regardless - DeepSeek "
     "has no vision model."),
    ("DEEPSEEK_API_KEY", "DeepSeek API key", "secret", True,
     "Required if the option above is on."),
]
FIELD_NAMES = {name for name, *_ in FIELDS}


def is_configured():
    return all(os.environ.get(k) for k in REQUIRED)


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
            "type": ftype,
            "advanced": advanced,
            "help": help_text,
            "set": bool(os.environ.get(name)),
            "value": ((os.environ.get(name) or "").lower() in TRUE_STRINGS) if ftype == "bool" else None,
            "display": (_mask(os.environ.get(name, "")) if ftype == "secret" else os.environ.get(name, "")),
        }
        for name, label, ftype, advanced, help_text in FIELDS
    ]
    return {"configured": is_configured(), "fields": fields}


def _read_env_lines():
    if not os.path.exists(ENV_PATH):
        return []
    with open(ENV_PATH) as f:
        return f.read().splitlines()


def validate_openrouter_key(key):
    """Real check via OpenRouter's key-info endpoint, not just "is it non-empty".
    Catches a bad/revoked key immediately, and specifically catches pasting a
    provisioning/management key by mistake - that exact mistake silently broke
    the vision fallback for a full day earlier in this project (it 401s on every
    real request but looks superficially like a valid key)."""
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read()).get("data", {})
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "That OpenRouter key was rejected (401) - double check you copied it correctly."
        return True, None  # OpenRouter having a bad moment shouldn't block saving a fine key
    except Exception:
        return True, None  # network hiccup - don't block saving over the validation step itself

    if data.get("is_provisioning_key") or data.get("is_management_key"):
        return False, (
            "That's an OpenRouter provisioning/management key, not a regular API key - it "
            "can't generate anything. Create a normal key at openrouter.ai/keys (the default "
            "\"Create Key\" button, not a provisioning key)."
        )
    return True, None


def save_config(updates):
    """Apply `updates` (name -> value; "" clears that key) to .env and the live
    process. Unknown keys are ignored. Existing lines/comments/ordering in .env
    are preserved; new keys are appended.

    Validates the RESULTING state before writing anything, so a rejected save
    never partially applies: OpenRouter must end up set, and if "use DeepSeek
    for text" ends up on, a DeepSeek key must be set too."""
    updates = {k: (v or "").strip() for k, v in updates.items() if k in FIELD_NAMES}

    def resulting(key):
        return updates[key] if key in updates else (os.environ.get(key) or "")

    if not resulting("OPENROUTER_API_KEY"):
        return {**current_config(), "error": "An OpenRouter API key is required."}

    use_deepseek = resulting("USE_DEEPSEEK_FOR_TEXT").lower() in TRUE_STRINGS
    if use_deepseek and not resulting("DEEPSEEK_API_KEY"):
        return {**current_config(), "error": "\"Use DeepSeek for text\" needs a DeepSeek API key too."}

    if updates.get("OPENROUTER_API_KEY"):
        ok, err = validate_openrouter_key(updates["OPENROUTER_API_KEY"])
        if not ok:
            return {**current_config(), "error": err}

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
