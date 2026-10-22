import base64
import os
import json
import re
import time

import openai
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Everyone uses OpenRouter + one model, for both text and vision. The model is
# picked in Settings (OPENROUTER_MODEL) so people aren't stuck when Gemini has a
# bad day; every choice must accept images, since vision uses it too.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-3.7-flash"
# Different vendor than the default, so a Google outage doesn't take both down.
BACKUP_MODEL = "anthropic/claude-haiku-5.5"
# Suggestions shown in Settings - all image-capable and priced near the default.
# Any other OpenRouter model id can still be typed in.
SUGGESTED_MODELS = [
    DEFAULT_MODEL,
    BACKUP_MODEL,
    "openai/gpt-5.6-luna",
    "qwen/qwen3.8-flash",
    "google/gemini-3.8-flash",
]

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


def openrouter_model():
    return (os.environ.get("OPENROUTER_MODEL") or "").strip() or DEFAULT_MODEL


def _openrouter_fallback(model):
    """OpenRouter's native `models` routing: if the chosen model errors, is
    rate-limited, or is down, OpenRouter retries the next one server-side."""
    backup = BACKUP_MODEL if model != BACKUP_MODEL else DEFAULT_MODEL
    return {"models": [model, backup]}


def active_model():
    return "openrouter", openrouter_model()


def get_client():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not set. Add it in Settings — get one at openrouter.ai/keys."
        )
    base_url = OPENROUTER_BASE_URL
    # max_retries=0: _complete() owns retry policy (the SDK's would also retry
    # timeouts, which can bill twice for one answer).
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


class LLMError(RuntimeError):
    """An AI request failed in a way the user can act on. main.py shows the
    message as-is instead of a generic 'something went wrong'."""


def _friendly_api_error(e):
    """Turn an SDK exception into an LLMError with an actionable message -
    'error code 401' means nothing to a non-technical user."""
    status = getattr(e, "status_code", None)
    # Prefer the provider's own message over the SDK's "Error code: 400 - {raw dict}".
    body = getattr(e, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    detail = body.get("message") if isinstance(body, dict) else None
    detail = (detail or getattr(e, "message", None) or str(e))[:300]
    if isinstance(e, openai.APITimeoutError):
        msg = "The AI took too long to respond. Try again - if it keeps happening, pick a different model in Settings."
    elif isinstance(e, openai.APIConnectionError):
        msg = "Couldn't reach the AI provider. Check your internet connection (a VPN or firewall can block it)."
    elif status == 401:
        msg = "The API key was rejected (401). It may be invalid, revoked, or the wrong kind of key - check it in Settings."
    elif status == 402 or "credit" in detail.lower() or "insufficient" in detail.lower():
        msg = ("The API request was rejected for billing (402) - the account is likely out of credit. "
               "Add more at openrouter.ai and try again.")
    elif status == 429:
        msg = "The AI provider is rate-limiting requests right now. Wait a minute and try again."
    elif status and status >= 500:
        msg = "The AI provider is having problems right now. Try again shortly, or pick a different model in Settings."
    elif status in (400, 404) and "model" in detail.lower():
        msg = f"The selected model isn't available ({detail}). Pick a different model in Settings."
    else:
        msg = f"The AI request failed: {detail}"
    return LLMError(msg)


# Not billed when they fail, so safe to retry. Timeouts are NOT retried: the
# provider may have finished (and charged for) the answer we gave up on.
_TRANSIENT = (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError)


class LLMTruncated(LLMError):
    """Reply hit max_tokens. Resending the same request would just truncate again."""


def _complete(client, kind, provider_name, fail_on_length=False, **kwargs):
    """One chat completion with retries on transient failures. Returns the
    reply text, "" if the model sent back nothing (chat_json retries that)."""
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(**kwargs)
            break
        except openai.APITimeoutError as e:
            raise _friendly_api_error(e)
        except _TRANSIENT as e:
            if attempt == 2:
                raise _friendly_api_error(e)
            time.sleep(2 * 2 ** attempt)
        except Exception as e:
            raise _friendly_api_error(e)
    # resp.model is what actually answered - may be the fallback, not kwargs["model"].
    _record_usage(resp, kind, provider_name=provider_name, model_name=getattr(resp, "model", None))
    # OpenRouter can return 200 with no choices (upstream error) or null content
    # (e.g. a safety block, or reasoning that ate the whole token budget).
    if not resp.choices:
        return ""
    if fail_on_length and getattr(resp.choices[0], "finish_reason", None) == "length":
        raise LLMTruncated("The AI's reply was cut off before it finished.")
    return (resp.choices[0].message.content or "").strip()


def chat(prompt, system=None, temperature=0.7, max_tokens=2000, json_mode=False, kind=None):
    client = get_client()
    provider, model = active_model()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if provider == "openrouter":
        kwargs["extra_body"] = _openrouter_fallback(model)
    return _complete(client, kind, provider, fail_on_length=json_mode, **kwargs)


def chat_json(prompt, system=None, temperature=0.7, max_tokens=4000, retries=2, kind=None):
    """Ask the model for strict JSON output and parse it.

    JSON mode is on from the first attempt (it used to be enabled only on retry,
    which bought a guaranteed second call every time the first reply was fenced).
    Only malformed output is retried — an auth/quota/bad-request error will fail
    the same way three times, so it is raised immediately.
    """
    # The literal lowercase "json" is required: some providers reject
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
    bumped = False
    for _ in range(retries + 1):
        # API-level failures raise straight out of chat(): retrying the same
        # request here cannot fix them (chat already retried transient ones).
        try:
            raw = chat(
                prompt, system=sys, temperature=temperature,
                max_tokens=max_tokens, json_mode=True, kind=kind,
            )
        except LLMTruncated:
            # Cut off at max_tokens: an identical resend truncates again. Retry
            # once with a bigger budget, then give up rather than pay 3x.
            if bumped:
                raise LLMError("The AI's reply was too long to finish. Try fewer slides or a different model in Settings.")
            bumped = True
            max_tokens = min(max_tokens * 2, 16000)
            continue
        try:
            return _parse_json(raw)
        except (ValueError, TypeError) as e:
            last_err = e
    raise LLMError(
        f"The AI sent back an unreadable reply {retries + 1} times in a row. Try again, "
        "or pick a different model in Settings."
    ) from last_err


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
    model = openrouter_model()
    return _complete(
        client, kind, "openrouter",
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
        extra_body=_openrouter_fallback(model),
    )


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
