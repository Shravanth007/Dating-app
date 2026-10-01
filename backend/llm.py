"""Talking to the model through OpenRouter (OpenAI-compatible chat completions).

- ask():       one question → text, or JSON validated against a pydantic schema
- run_agent(): a real tool-using agent loop: the model decides which tool to call, we run it, feed the result back,
               until the model stops calling tools (or hits its step limit)
Every call's cost is added to state["spend"]; once it reaches BUDGET_USD no further call is made."""
import json, threading, time, urllib.error

from .config import (BUDGET_USD, FALLBACK_MODELS, KEEP_TOOL_RESULTS, MAX_CALLS, MIN_SECONDS_BETWEEN_CALLS, MODEL,
                     OPENROUTER_API_KEY, TOOL_RESULT_CHARS)
from .store import lock, save, state
from .tools.fetch import http


class BudgetExceeded(RuntimeError):
    pass


_pace, _last_call, _caps = threading.Lock(), [0.0], {}


def supports(model, param):
    """Does this OpenRouter model support a request parameter (e.g. "structured_outputs")? Looked up once."""
    if model not in _caps:
        try:
            models = json.loads(http("https://openrouter.ai/api/v1/models"))["data"]
            _caps.update({m["id"]: set(m.get("supported_parameters") or []) for m in models})
        except Exception:
            pass
    return param in _caps.get(model, ())


def _wait_turn():
    """Free models allow ~20 requests/minute: space calls out instead of getting 429s."""
    with _pace:
        delay = _last_call[0] + MIN_SECONDS_BETWEEN_CALLS - time.time()
        if delay > 0:
            time.sleep(delay)
        _last_call[0] = time.time()


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


def chat(messages, tools=None, schema=None, web=False, effort="low", max_tokens=8000, model=None):
    """One OpenRouter call with retries. Returns the assistant message dict (content, tool_calls, …)."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    if state["spend"]["usd"] >= BUDGET_USD:
        raise BudgetExceeded(f"Spending cap reached (${BUDGET_USD:.2f}). Raise BUDGET_USD to continue.")
    if state["spend"]["calls"] >= MAX_CALLS:
        raise BudgetExceeded(f"Call cap reached ({MAX_CALLS} model calls). Raise MAX_CALLS to continue.")
    model = model or MODEL
    body = {"model": model, "models": [model, *[m for m in FALLBACK_MODELS if m != model]], "messages": messages,
            "max_tokens": max_tokens, "reasoning": {"effort": effort}, "usage": {"include": True}}
    if tools:
        body["tools"] = tools
    if schema and supports(model, "structured_outputs"):
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "strict": True, "schema": strict(schema.model_json_schema())}}
    if web:  # OpenRouter's web search plugin (uses the model's native search for Anthropic models)
        body["plugins"] = [{"id": "web", "max_results": 8}]
    err = None
    for attempt in range(6):
        _wait_turn()
        try:
            r = json.loads(http("https://openrouter.ai/api/v1/chat/completions", body, timeout=300, headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",
                "X-Title": "Proxy Hearts"}))
            if "error" in r:
                raise RuntimeError(r["error"].get("message", r["error"]))
            with lock:
                state["spend"]["usd"] = round(state["spend"]["usd"] + float((r.get("usage") or {}).get("cost") or 0), 4)
                state["spend"]["calls"] += 1
            save()
            return r["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            err = RuntimeError(f"OpenRouter {e.code}: {e.read().decode()[:300]}")
            if e.code in (400, 401, 402, 403):
                raise err
            time.sleep(15 * (attempt + 1) if e.code == 429 else 2 ** attempt)  # 429 = free-model rate limit
            continue
        except Exception as e:  # network hiccup / overloaded → retry with backoff
            err = e
        time.sleep(2 ** attempt)
    raise err


def ask(system, user, schema=None, **kw):
    """Single-turn helper. With `schema` (a pydantic model) returns a validated dict, else plain text."""
    if schema:  # always state the format too: some free models ignore response_format
        system += ("\n\nReply with ONLY a JSON object (no prose, no code fences) matching this JSON schema:\n"
                   + json.dumps(schema.model_json_schema()))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(3):
        msg = chat(messages, schema=schema, **kw)
        text = (msg.get("content") or "").strip()
        if not schema:
            return text
        try:
            return schema.model_validate_json(text[text.find("{"):text.rfind("}") + 1]).model_dump()
        except ValueError as e:
            if attempt == 2:
                raise
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That JSON was invalid ({str(e)[:300]}). "
                                                     "Reply with the corrected JSON only."}]


def run_agent(system, task, tools, on_event=print, max_steps=20, effort="medium", model=None):
    """tools: {name: (description, json_schema_of_args, python_function)}.
    The agent stops when it answers without calling a tool, calls `finish`, or hits max_steps. Returns its last text."""
    specs = [{"type": "function", "function": {"name": n, "description": d, "parameters": p}}
             for n, (d, p, _) in tools.items()]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    for _ in range(max_steps):
        msg = chat(messages, tools=specs, effort=effort, max_tokens=8000, model=model)
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
            except BudgetExceeded:
                raise
            except Exception as e:  # tell the agent what went wrong; it can adapt
                result = {"error": str(e)[:300]}
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_CHARS]})
        # keep long loops cheap: older tool results shrink to a stub (the agent already acted on them)
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        for m in tool_msgs[:-KEEP_TOOL_RESULTS]:
            if len(m["content"]) > 400:
                m["content"] = m["content"][:400] + " …(older result trimmed)"
        if any(c["function"]["name"] == "finish" for c in msg["tool_calls"]):
            return ""
    on_event("⏱️ step limit reached")
    return ""
