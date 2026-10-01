"""LLM calls via OpenRouter with JSON-schema output validated by pydantic."""
import json, os, re, time, urllib.error

from .core import MODEL, http


def strict(schema):
    """OpenRouter/Anthropic strict json_schema: every object closed, every property required."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
            schema["required"] = list(schema.get("properties", {}))
        for v in schema.values():
            strict(v)
    elif isinstance(schema, list):
        for v in schema:
            strict(v)
    return schema


def llm(system, user, schema=None, effort="low", max_tokens=8000):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    body = {"model": MODEL, "max_tokens": max_tokens, "reasoning": {"effort": effort},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "strict": True, "schema": strict(schema.model_json_schema())}}
    err = None
    for attempt in range(4):
        try:
            r = json.loads(http("https://openrouter.ai/api/v1/chat/completions", body, timeout=300, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "Proxy Hearts"}))
            if "error" in r:
                raise RuntimeError(r["error"].get("message", r["error"]))
            text = r["choices"][0]["message"]["content"].strip()
            if not schema:
                return text
            text = re.sub(r"^```(?:json)?|```$", "", text).strip()
            return schema.model_validate_json(text).model_dump()
        except urllib.error.HTTPError as e:
            err = RuntimeError(f"OpenRouter {e.code}: {e.read().decode()[:300]}")
            if e.code in (400, 401, 402, 403):
                raise err
        except Exception as e:  # bad JSON / transient network → retry
            err = e
        time.sleep(2 ** attempt)
    raise err
