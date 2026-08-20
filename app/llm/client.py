import base64
import os
import json
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Everyone defaults to OpenRouter + one fixed model, for both text and vision -
# no per-user provider/model picking anymore. This model is multimodal (text +
# image in one call), so text generation and the vision fallback both use it;
# that also means there's a real path later to fold vision straight into the
# question/summary prompts instead of a separate captioning pass, though
# that's not done here - this change is just the provider/config simplification.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-3.7-flash"

# Advanced, opt-in, TEXT-ONLY override for people who already have DeepSeek
# credit to use up (see app/config.py for the toggle + migration). Vision
# always goes through OpenRouter regardless - DeepSeek has no vision model.
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

MAX_OCR_CHARS_FOR_PROMPT = 1200


def cap_for_prompt(text, max_chars=MAX_OCR_CHARS_FOR_PROMPT):
    """Truncate OCR text before it goes into an LLM prompt. OCR has no natural
    length limit - one dense scanned/screenshotted slide can produce
    thousands of characters, often noisy - and this was going into summary
    and question-generation prompts completely uncapped, sometimes multiplying
    a single lecture's cost several times over. Only for prompt-building; full
    OCR text is still stored and shown as-is anywhere it's displayed to the user."""
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[...OCR text truncated, slide had unusually dense image text...]"


def _use_deepseek_for_text():
    enabled = (os.environ.get("USE_DEEPSEEK_FOR_TEXT") or "").strip().lower() in ("1", "true", "yes")
    return enabled and bool(os.environ.get("DEEPSEEK_API_KEY"))


def active_model():
    if _use_deepseek_for_text():
        return "deepseek", DEEPSEEK_MODEL
    return "openrouter", DEFAULT_MODEL


def get_client():
    if _use_deepseek_for_text():
        # _use_deepseek_for_text() already requires DEEPSEEK_API_KEY to be set
        # to return True, so it's guaranteed present here.
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        base_url = DEEPSEEK_BASE_URL
    else:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set. Add it in Settings — get one at openrouter.ai/keys."
            )
        base_url = OPENROUTER_BASE_URL
    # max_retries=0: chat_json owns retry policy. The SDK default (2) multiplies with
    # it, turning one logical call into up to 9 billed requests.
    return OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=180)


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
            name, model = active_model()
            provider_name = provider_name or name
            model_name = model_name or model
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


def _friendly_api_error(e):
    """Re-raise API errors with an actionable message instead of a raw SDK
    exception - 'error code 401' means nothing to a non-technical user, but
    'your OpenRouter key looks invalid or has run out of credit' does."""
    msg = str(e)
    status = getattr(e, "status_code", None)
    if status == 401 or "401" in msg:
        return RuntimeError(
            "The API key was rejected (401). It may be invalid, revoked, or the wrong "
            "kind of key - check it in Settings."
        )
    if status == 402 or "402" in msg or "credit" in msg.lower() or "insufficient" in msg.lower():
        return RuntimeError(
            "The API request was rejected for billing (402) - the account is likely out "
            "of credit. Add more at openrouter.ai (or platform.deepseek.com) and try again."
        )
    return e


def chat(prompt, system=None, temperature=0.7, max_tokens=2000, json_mode=False, kind=None):
    client = get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs = dict(
        model=active_model()[1],
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        raise _friendly_api_error(e)
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
        "\n\nThe app's renderer only supports plain text/basic markdown (bold, italic, "
        "headers, tables) - it does NOT render LaTeX. Never use LaTeX math delimiters "
        "($...$, \\text{}, \\times, \\Delta, etc). Write formulas in plain text/unicode "
        "instead, e.g. \"ΔP = Q × R\" and \"P_ip\" or \"P(ip)\" for subscripts."
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
    """Describe a local image via OpenRouter. Returns the model's text reply, or
    "" if OPENROUTER_API_KEY is unset (vision fallback is optional, not required)."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return ""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "jpeg"
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, max_retries=0, timeout=180)
    try:
        resp = client.chat.completions.create(
            model=DEFAULT_MODEL,
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
    except Exception as e:
        raise _friendly_api_error(e)
    _record_usage(resp, kind, provider_name="openrouter", model_name=DEFAULT_MODEL)
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
