import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def get_client():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not set. Export it before running, e.g. "
            "export DEEPSEEK_API_KEY=sk-..."
        )
    return OpenAI(api_key=api_key, base_url=BASE_URL)


def chat(prompt, system=None, temperature=0.7, max_tokens=2000, json_mode=False):
    client = get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs = dict(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()


def chat_json(prompt, system=None, temperature=0.7, max_tokens=4000, retries=2):
    """Ask the model for strict JSON output and parse it. Retries with JSON mode."""
    sys = (system or "") + (
        "\n\nRespond with ONLY a valid JSON object. No markdown fences, no commentary."
    )
    last_err = None
    for attempt in range(retries + 1):
        try:
            raw = chat(
                prompt, system=sys, temperature=temperature,
                max_tokens=max_tokens, json_mode=(attempt > 0),
            )
            parsed = _parse_json(raw)
            return parsed
        except Exception as e:
            last_err = e
    raise last_err


def _parse_json(raw):
    import re as _re
    raw = raw.strip()
    if raw.startswith("```"):
        raw = _re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = _re.sub(r"\n?```$", "", raw)
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


import re
