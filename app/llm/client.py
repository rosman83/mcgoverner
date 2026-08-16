import base64
import os
import json
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Vision fallback for slides whose images carry no readable OCR text (research-paper
# figures, anatomy diagrams). Always routed through OpenRouter, independent of whichever
# text PROVIDERS entry is active — vision needs a vision-capable model regardless of
# which provider is answering text prompts.
VISION_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_VISION_MODEL = "google/gemini-2.0-flash-001"

# DeepSeek is billed direct. OpenRouter is a single OpenAI-compatible gateway to
# everything else (alternate text models, and the vision fallback below) — one key
# instead of a separate subscription per provider.
PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat",
        "key_env": "OPENROUTER_API_KEY",
    },
}


def _provider():
    """Resolve the active provider. LLM_PROVIDER picks it; if unset, whichever key
    is present wins (DeepSeek first, for backwards compatibility)."""
    name = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if name not in PROVIDERS:
        name = "deepseek" if os.environ.get("DEEPSEEK_API_KEY") else (
            "openrouter" if os.environ.get("OPENROUTER_API_KEY") else "deepseek"
        )
    p = dict(PROVIDERS[name], name=name)
    # Per-provider overrides, plus the legacy DEEPSEEK_* names.
    p["base_url"] = os.environ.get("LLM_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or p["base_url"]
    p["model"] = os.environ.get("LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL") or p["model"]
    return p


def active_model():
    p = _provider()
    return p["name"], p["model"]


def get_client():
    p = _provider()
    api_key = os.environ.get(p["key_env"])
    if not api_key:
        raise RuntimeError(
            f"{p['key_env']} not set for provider '{p['name']}'. Add it to .env, e.g. "
            f"{p['key_env']}=sk-..."
        )
    # max_retries=0: chat_json owns retry policy. The SDK default (2) multiplies with
    # it, turning one logical call into up to 9 billed requests.
    return OpenAI(api_key=api_key, base_url=p["base_url"], max_retries=0, timeout=180)


def _record_usage(resp, kind, provider_name=None, model_name=None):
    """Log token usage so spend is visible in the UI. Never let accounting break a call."""
    try:
        u = resp.usage
        if not u:
            return
        cached = 0
        details = getattr(u, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        # DeepSeek reports cache hits on the usage object directly.
        cached = cached or getattr(u, "prompt_cache_hit_tokens", 0) or 0
        from app.db import get_conn

        if provider_name is None or model_name is None:
            p = _provider()
            provider_name = provider_name or p["name"]
            model_name = model_name or p["model"]
        conn = get_conn()
        conn.execute(
            "INSERT INTO api_usage(provider, model, kind, prompt_tokens, cached_tokens, "
            "completion_tokens) VALUES(?,?,?,?,?,?)",
            (provider_name, model_name, kind, u.prompt_tokens or 0, cached,
             u.completion_tokens or 0),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"usage logging failed: {e}")


def chat(prompt, system=None, temperature=0.7, max_tokens=2000, json_mode=False, kind=None):
    client = get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs = dict(
        model=_provider()["model"],
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    _record_usage(resp, kind)
    return resp.choices[0].message.content.strip()


def chat_json(prompt, system=None, temperature=0.7, max_tokens=4000, retries=2, kind=None):
    """Ask the model for strict JSON output and parse it.

    JSON mode is on from the first attempt (it used to be enabled only on retry,
    which bought a guaranteed second call every time the first reply was fenced).
    Only malformed output is retried — an auth/quota/bad-request error will fail
    the same way three times, so it is raised immediately.
    """
    # The literal lowercase "json" is required: DeepSeek (like OpenAI) rejects
    # response_format=json_object unless the word appears in the messages, and this
    # path now sets that format on every call.
    sys = (system or "") + (
        "\n\nRespond with ONLY a valid json object. No markdown fences, no commentary."
    )
    last_err = None
    for _ in range(retries + 1):
        try:
            raw = chat(
                prompt, system=sys, temperature=temperature,
                max_tokens=max_tokens, json_mode=True, kind=kind,
            )
        except Exception:
            raise  # API-level failure: retrying the same request cannot fix it
        try:
            return _parse_json(raw)
        except (ValueError, TypeError) as e:
            last_err = e
    raise last_err


def describe_image(image_path, prompt, max_tokens=500, kind="vision"):
    """Describe a local image via an OpenRouter vision model. Returns the model's
    text reply, or "" if OPENROUTER_API_KEY is unset (vision fallback is optional,
    not required)."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return ""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "jpeg"
    model = os.environ.get("OPENROUTER_VISION_MODEL") or DEFAULT_VISION_MODEL
    client = OpenAI(api_key=api_key, base_url=VISION_BASE_URL, max_retries=0, timeout=180)
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
            ],
        }],
        max_tokens=max_tokens,
        temperature=0.3,
    )
    _record_usage(resp, kind, provider_name="openrouter-vision", model_name=model)
    return resp.choices[0].message.content.strip()


def _parse_json(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    # fallback: find the outermost {...} object
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError(f"Could not parse JSON from model output: {raw[:200]}")
