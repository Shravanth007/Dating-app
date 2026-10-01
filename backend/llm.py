"""Talking to the model through OpenRouter (OpenAI-compatible chat completions).

- ask():       one question → text, or JSON validated against a pydantic schema
- run_agent(): a real tool-using agent loop: the model decides which tool to call, we run it, feed the result back,
               until the model stops calling tools (or runs out of steps)."""
import json, re, time, urllib.error

from .config import MODEL, OPENROUTER_API_KEY
from .tools.fetch import http


def strict(schema):
    """Strict JSON-schema mode needs every object closed and every property required."""
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


def chat(messages, tools=None, schema=None, web=False, effort="low", max_tokens=8000):
    """One OpenRouter call with retries. Returns the assistant message dict (content, tool_calls, …)."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens, "reasoning": {"effort": effort}}
    if tools:
        body["tools"] = tools
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "strict": True, "schema": strict(schema.model_json_schema())}}
    if web:  # OpenRouter's web search plugin (uses the model's native search for Anthropic models)
        body["plugins"] = [{"id": "web", "max_results": 10}]
    err = None
    for attempt in range(4):
        try:
            r = json.loads(http("https://openrouter.ai/api/v1/chat/completions", body, timeout=300, headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",
                "X-Title": "Proxy Hearts"}))
            if "error" in r:
                raise RuntimeError(r["error"].get("message", r["error"]))
            return r["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            err = RuntimeError(f"OpenRouter {e.code}: {e.read().decode()[:300]}")
            if e.code in (400, 401, 402, 403):
                raise err
        except Exception as e:  # network hiccup / overloaded → retry with backoff
            err = e
        time.sleep(2 ** attempt)
    raise err


def ask(system, user, schema=None, **kw):
    """Single-turn helper. With `schema` (a pydantic model) returns a validated dict, else plain text."""
    for attempt in range(2):
        msg = chat([{"role": "system", "content": system}, {"role": "user", "content": user}], schema=schema, **kw)
        text = (msg.get("content") or "").strip()
        if not schema:
            return text
        try:
            return schema.model_validate_json(re.sub(r"^```(?:json)?|```$", "", text).strip()).model_dump()
        except ValueError:
            if attempt:
                raise


def run_agent(system, task, tools, on_event=print, max_steps=20, effort="medium"):
    """tools: {name: (description, json_schema_of_args, python_function)}.
    Returns the agent's final text. Every tool call and its outcome is reported through on_event(text)."""
    specs = [{"type": "function", "function": {"name": n, "description": d, "parameters": p}}
             for n, (d, p, _) in tools.items()]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    for _ in range(max_steps):
        msg = chat(messages, tools=specs, effort=effort, max_tokens=8000)
        messages.append({k: v for k, v in msg.items() if v is not None and k != "refusal"})
        if msg.get("content") and msg.get("tool_calls"):
            on_event(f"💭 {msg['content'].strip()[:300]}")
        if not msg.get("tool_calls"):
            return (msg.get("content") or "").strip()
        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
                result = tools[name][2](**args)
            except Exception as e:  # tell the agent what went wrong; it can adapt
                result = {"error": str(e)[:300]}
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)[:12000]})
        if any(c["function"]["name"] == "finish" for c in msg["tool_calls"]):
            return ""
    return ""
