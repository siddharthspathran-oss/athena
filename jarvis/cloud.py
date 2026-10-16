"""Answering class work through a hosted model, when waiting is the problem.

Everything else in Athena runs on this machine and stays on it. This is the
one deliberate exception: a probability worksheet took the better part of an
hour on an 8GB card, because the model that gets it right reasons at length
and spills onto the CPU while doing so. A hosted model answers the same
worksheet in under a minute.

What that costs is privacy, and only you can decide whether the trade is
worth it: the question and any attached document are sent to whichever
provider you configure. So it is opt-in, scoped to `study.model` alone, and
voice, apps, messages and files never come near it.

Keys live in Windows Credential Manager ("Athena/<provider>", credstore.py;
data/api_keys.json until 16 Oct 2026) - never config.yaml, which is the file
you would paste into a chat or commit to a repo. An environment variable
(GEMINI_API_KEY, GROQ_API_KEY, ...) is used in preference if one is set.

Configure with a prefix on the model name:

    study:
      model: "groq:llama-3.3-70b-versatile"
      model: "gemini:gemini-2.0-flash"
      model: "openrouter:deepseek/deepseek-r1:free"
      model: "cerebras:llama-3.3-70b"
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .config import DATA
from .store import load_json, save_json

KEYS_FILE = DATA / "api_keys.json"

# Everything except Gemini speaks the OpenAI chat-completions shape, so one
# adapter covers all of them and a new provider is usually just a line here.
PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "env": "GROQ_API_KEY",
        "signup": "https://console.groq.com/keys",
        "note": "Fastest of the free tiers by a wide margin.",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "env": "GEMINI_API_KEY",
        "signup": "https://aistudio.google.com/apikey",
        "note": "Generous free tier and strong at maths.",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "env": "OPENROUTER_API_KEY",
        "signup": "https://openrouter.ai/keys",
        "note": "Many models; the ones ending :free cost nothing.",
    },
    "nvidia": {
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "env": "NVIDIA_API_KEY",
        "signup": "https://build.nvidia.com/",
        "note": "NVIDIA NIM: big open models (Llama, Qwen, DeepSeek, Nemotron).",
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "env": "ANTHROPIC_API_KEY",
        "signup": "https://console.anthropic.com/settings/keys",
        "note": "Claude (Haiku is fast and very good at picking tools). Paid per token.",
    },
    "cerebras": {
        "url": "https://api.cerebras.ai/v1/chat/completions",
        "env": "CEREBRAS_API_KEY",
        "signup": "https://cloud.cerebras.ai/",
        "note": "Very fast, smaller model choice.",
    },
}


def split(model: str) -> tuple[str, str]:
    """("groq", "llama-3.3-70b") from "groq:llama-3.3-70b", or ("", model).

    A bare name means Ollama, so nothing here changes unless a prefix is
    written deliberately.
    """
    name = (model or "").strip()
    head, _, rest = name.partition(":")
    if head.lower() in PROVIDERS and rest:
        return head.lower(), rest
    return "", name


def is_cloud(model: str) -> bool:
    return bool(split(model)[0])


# ---------- keys ----------

def get_key(provider: str) -> str:
    """Environment first, then Windows Credential Manager (credstore.py), then
    the old data/api_keys.json - which move_keys_to_vault() empties."""
    from . import credstore
    env = PROVIDERS.get(provider, {}).get("env", "")
    return (os.environ.get(env, "") or credstore.get(provider) or
            load_json(KEYS_FILE, {}).get(provider, "")).strip()


def move_keys_to_vault() -> tuple[int, int]:
    """Move every key out of data/api_keys.json into Credential Manager, each
    checked by reading it back before it leaves the file. (moved, left)."""
    from . import credstore
    keys = load_json(KEYS_FILE, {})
    moved = 0
    for provider in list(keys):
        value = str(keys[provider]).strip()
        if value and credstore.put(provider, value) and credstore.get(provider) == value:
            del keys[provider]
            moved += 1
    save_json(KEYS_FILE, keys)
    return moved, len(keys)


def set_key(provider: str, key: str) -> None:
    from . import credstore
    clean = "".join(c for c in key if c.isprintable()).strip()
    if credstore.put(provider, clean) and credstore.get(provider) == clean:     # Credential Manager first
        keys = load_json(KEYS_FILE, {})
        if keys.pop(provider, None) is not None:
            save_json(KEYS_FILE, keys)
        return
    keys = load_json(KEYS_FILE, {})                      # not Windows: the file, as before
    # Pasting into a hidden prompt can keep the Ctrl+V keystrokes () - drop control characters.
    keys[provider] = "".join(c for c in key if c.isprintable()).strip()
    save_json(KEYS_FILE, keys)
    try:                       # best effort: keep it out of other accounts
        KEYS_FILE.chmod(0o600)
    except OSError:
        pass


def how_to_get_one(provider: str) -> str:
    p = PROVIDERS.get(provider)
    if not p:
        return (f"'{provider}' is not a provider I know. "
                f"Try one of: {', '.join(sorted(PROVIDERS))}.")
    return (f"No {provider} key yet.\n\n"
            f"1. Get a free one at {p['signup']}\n"
            f"2. Save it with:  python run.py key {provider} <the-key>\n"
            f"   (or set {p['env']} in your environment)\n\n"
            f"{p['note']}\n"
            f"Your questions and any attached document are sent to "
            f"{provider} when this is in use.")


# ---------- talking to them ----------

def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    from . import deadline
    if deadline.expired():
        raise RuntimeError("out of time for this request")
    timeout = deadline.cap(timeout)
    body = json.dumps(payload).encode()
    # A User-Agent is not optional. urllib sends "Python-urllib/3.13" by
    # default, and Groq sits behind Cloudflare, which answers that with 403
    # error 1010 - a ban, not a rate limit, so retrying never helps. The model
    # listing worked throughout because it happened to set one.
    request = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": "Athena/1.0 (local assistant)",
        **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"{e.code} from the provider: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach the provider ({e.reason})") from None
    except (TimeoutError, ConnectionError) as e:
        # A reply that stalls mid-read is a bare TimeoutError, not a URLError:
        # it slipped past every caller's fallback (16 Oct 2026, NVIDIA Ultra).
        raise RuntimeError(f"the provider timed out ({type(e).__name__})") from None


def _gemini(model: str, key: str, messages: list[dict], temperature: float,
            max_tokens: int, timeout: int) -> str:
    """Gemini takes a different shape: a system line of its own, and turns
    labelled 'user' and 'model' rather than 'assistant'."""
    system = "\n\n".join(m["content"] for m in messages
                         if m.get("role") == "system" and m.get("content"))
    turns = []
    for m in messages:
        if m.get("role") not in ("user", "assistant") or not m.get("content"):
            continue
        turns.append({"role": "user" if m["role"] == "user" else "model",
                      "parts": [{"text": m["content"]}]})
    # Gemini's Flash models reason before answering, and those thoughts are
    # charged against maxOutputTokens. Measured on one algebra question:
    # 2,286 thinking tokens against 1,193 of actual answer - so a 6,000 budget
    # leaves under 4,000 for the reply, and a harder question returned a
    # 643-character stub that scored nothing. The answer gets its own budget;
    # thinking is given room beside it rather than out of it.
    payload: dict[str, Any] = {
        "contents": turns,
        "generationConfig": {"temperature": temperature,
                             "maxOutputTokens": max_tokens * 3},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    url = f"{PROVIDERS['gemini']['url']}/{model}:generateContent"
    data = _post(url, payload, {"x-goog-api-key": key}, timeout)
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError):
        blocked = data.get("promptFeedback", {}).get("blockReason")
        raise RuntimeError(f"no answer returned"
                           f"{f' ({blocked})' if blocked else ''}") from None


def _openai_shape(provider: str, model: str, key: str, messages: list[dict],
                  temperature: float, max_tokens: int, timeout: int) -> str:
    clean = [{"role": m["role"], "content": m["content"]} for m in messages
             if m.get("role") in ("system", "user", "assistant")
             and m.get("content")]
    payload = {"model": model, "messages": clean, "temperature": temperature, "max_tokens": max_tokens}
    if provider == "nvidia":        # the planner's Ultra too: 9-12 s a step thinking (13 Oct 2026)
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    data = _post(PROVIDERS[provider]["url"], payload,
                 {"Authorization": f"Bearer {key}"}, timeout)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise RuntimeError("no answer returned") from None


def chat_tools(model: str, messages: list[dict], tools: list[dict] | None,
               temperature: float = 0.3, max_tokens: int = 600, timeout: int = 30):
    """One agent turn with tool calling, on an OpenAI-shaped provider (not Gemini).

    Returns a message shaped like Ollama's (role, content, tool_calls with
    function.name / function.arguments as a dict), so agent.py doesn't change.
    Athena's history has tool results with no call ids: each assistant call
    gets an id here, and the results that follow take them in order; a result
    with no call before it (a router ran the tool) goes in as plain text.
    """
    from types import SimpleNamespace
    provider, name = split(model)
    key = get_key(provider)
    if not key:
        raise RuntimeError(how_to_get_one(provider))
    if provider == "anthropic":
        data = _anthropic(name, key, cloud_safe(messages), tools, temperature, max_tokens, timeout)
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [SimpleNamespace(function=SimpleNamespace(name=b["name"], arguments=b.get("input") or {}))
                 for b in blocks if b.get("type") == "tool_use"]
        return SimpleNamespace(role="assistant", content=_no_reasoning(text), tool_calls=calls or None)
    payload = _openai_payload(provider, name, messages, tools, temperature, max_tokens)
    data = _post(PROVIDERS[provider]["url"], payload, {"Authorization": f"Bearer {key}"}, timeout)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError):
        raise RuntimeError("no answer returned") from None
    return _as_message(msg.get("content") or "", [
        (c["function"]["name"], c["function"].get("arguments") or {}) for c in msg.get("tool_calls") or []])


LOCAL_ONLY = re.compile(r"\n?\[\[local-only\]\].*?\[\[/local-only\]\]\n?", re.S)


def cloud_safe(messages: list[dict]) -> list[dict]:
    """Messages that may leave this PC: turns marked private (WhatsApp texts,
    contacts, calls, email - handled by the local model) are left out, so a
    later question to a hosted model doesn't carry them along; and the
    [[local-only]] block of the system prompt (personal memories - people,
    birthdays, numbers) is cut out of it (15 Oct 2026)."""
    out = []
    for m in messages:
        if isinstance(m, dict) and m.get("private"):
            continue
        if isinstance(m, dict) and m.get("role") == "system" and "[[local-only]]" in str(m.get("content", "")):
            m = {**m, "content": LOCAL_ONLY.sub("\n", str(m["content"]))}
        out.append(m)
    return out


def _as_message(content: str, calls: list[tuple[str, Any]]):
    from types import SimpleNamespace
    out = []
    for fn, args in calls:
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        out.append(SimpleNamespace(function=SimpleNamespace(name=fn, arguments=args)))
    return SimpleNamespace(role="assistant", content=_no_reasoning(content), tool_calls=out or None)


def _openai_payload(provider: str, name: str, messages: list[dict], tools: list[dict] | None,
                    temperature: float, max_tokens: int) -> dict:
    out, pending, n = [], [], 0
    for m in cloud_safe(messages):
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                f = c["function"] if isinstance(c, dict) else c.function
                fn, args = (f["name"], f["arguments"]) if isinstance(f, dict) else (f.name, f.arguments)
                n += 1
                calls.append({"id": f"call_{n}", "type": "function", "function": {
                    "name": fn, "arguments": args if isinstance(args, str) else json.dumps(args or {})}})
                pending.append(f"call_{n}")
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            if pending:
                out.append({"role": "tool", "tool_call_id": pending.pop(0), "content": str(m.get("content", ""))})
            else:
                out.append({"role": "user", "content": f"[{m.get('name', 'tool')} result] {m.get('content', '')}"})
        elif role in ("system", "user", "assistant") and m.get("content"):
            out.append({"role": role, "content": m["content"]})
    payload = {"model": name, "messages": out, "temperature": temperature, "max_tokens": max_tokens}
    if provider == "nvidia":        # Nemotron thinks first: 7-17 s a reply with it, ~1 s without (13 Oct 2026)
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if tools:
        payload["tools"], payload["tool_choice"] = tools, "auto"
    return payload


def chat_tools_stream(model: str, messages: list[dict], tools: list[dict] | None, on_text,
                      stop=lambda: False, temperature: float = 0.3, max_tokens: int = 600, timeout: int = 30):
    """chat_tools(), streamed: each piece of the reply goes to on_text as it
    arrives, so the voice can start on the first sentence instead of waiting
    for the whole answer (15 Oct 2026). Tool calls are put back together from
    their pieces. Returns the same message as chat_tools()."""
    from . import deadline
    provider, name = split(model)
    key = get_key(provider)
    if not key:
        raise RuntimeError(how_to_get_one(provider))
    if provider in ("anthropic", "gemini"):          # not streamed here: whole reply, then spoken
        msg = chat_tools(model, messages, tools, temperature, max_tokens, timeout)
        if msg.content and not msg.tool_calls:
            on_text(msg.content)
        return msg
    if deadline.expired():
        raise RuntimeError("out of time for this request")
    payload = _openai_payload(provider, name, messages, tools, temperature, max_tokens)
    payload["stream"] = True
    req = urllib.request.Request(PROVIDERS[provider]["url"], data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Accept": "text/event-stream",
        "User-Agent": "Athena/1.0 (local assistant)", "Authorization": f"Bearer {key}"})
    text: list[str] = []
    calls: dict[int, list[str]] = {}
    try:
        with urllib.request.urlopen(req, timeout=deadline.cap(timeout)) as r:
            for raw in r:
                if stop():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                delta = ((chunk.get("choices") or [{}])[0] or {}).get("delta") or {}
                if delta.get("content"):
                    text.append(delta["content"])
                    on_text(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(int(tc.get("index", 0)), ["", ""])
                    f = tc.get("function") or {}
                    if f.get("name") and not slot[0]:
                        slot[0] = f["name"]
                    slot[1] += f.get("arguments") or ""
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} from the provider: {e.read().decode(errors='replace')[:300]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach the provider ({e.reason})") from None
    return _as_message("".join(text), [(c[0], c[1]) for _i, c in sorted(calls.items()) if c[0]])


# The model thinking out loud, which was then spoken (13 Oct 2026: "The user
# wants me to explain what a 4 year series is... I have the `explain` tool").
_REASONING = re.compile(r"^(?:okay,?\s+|so,?\s+|hmm,?\s+)?(?:the\s+user\s+(?:is\s+)?(?:wants?|asks?|asked|asking|said|says|mentioned|needs?|might|seems|wrote)\b|"
                        r"they\s+(?:mentioned|want|said|asked)\b|"
                        r"i\s+(?:should|need\s+to|will|could|must)\s+(?:call|use|check|respond|answer|reply)\b|"
                        r"i\s+have\s+(?:the\s+|a\s+)?`|let\s+me\s+think\b|we\s+need\s+to\b)", re.I)


def _no_reasoning(text: str) -> str:
    """Drop sentences that are the model reasoning about the request rather than answering it."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    kept = [s for s in parts if s.strip() and not _REASONING.match(s.strip())]
    return " ".join(kept) if len(kept) < len([s for s in parts if s.strip()]) else text


def _anthropic(model: str, key: str, messages: list[dict], tools: list[dict] | None,
               temperature: float, max_tokens: int, timeout: int) -> dict:
    """Claude's Messages API. Athena's history is OpenAI-shaped: the system
    prompt goes apart, a call becomes a tool_use block, its result a
    tool_result block in the next user turn (same-role turns merged). The
    system prompt and the tool list are marked for prompt caching - they are
    most of every request and barely change between turns."""
    system = "\n\n".join(str(m["content"]) for m in messages if m.get("role") == "system" and m.get("content"))
    out: list[dict] = []
    pending: list[str] = []
    n = 0

    def push(role: str, block: dict) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].append(block)
        else:
            out.append({"role": role, "content": [block]})

    def settle() -> None:                      # a call that never got a result still needs one
        while pending:
            push("user", {"type": "tool_result", "tool_use_id": pending.pop(0), "content": "(no result)"})

    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            settle()
            if m.get("content"):
                push("assistant", {"type": "text", "text": str(m["content"])})
            for c in m["tool_calls"]:
                f = c["function"] if isinstance(c, dict) else c.function
                fn, args = (f["name"], f["arguments"]) if isinstance(f, dict) else (f.name, f.arguments)
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except json.JSONDecodeError:
                        args = {}
                n += 1
                push("assistant", {"type": "tool_use", "id": f"toolu_{n:04d}", "name": fn, "input": args or {}})
                pending.append(f"toolu_{n:04d}")
        elif role == "tool":
            if pending:
                push("user", {"type": "tool_result", "tool_use_id": pending.pop(0), "content": str(m.get("content", ""))})
            else:
                push("user", {"type": "text", "text": f"[{m.get('name', 'tool')} result] {m.get('content', '')}"})
        elif role in ("user", "assistant") and m.get("content"):
            settle()
            push(role, {"type": "text", "text": str(m["content"])})
    settle()
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continuing)"}]})
    payload: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "messages": out}
    if system:
        payload["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if tools:
        payload["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                             "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                            for t in tools]
        payload["tools"][-1]["cache_control"] = {"type": "ephemeral"}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    try:
        return _post(PROVIDERS["anthropic"]["url"], payload, headers, timeout)
    except RuntimeError as e:
        if "temperature" not in str(e):
            raise
        payload.pop("temperature")             # a model that sets its own
        return _post(PROVIDERS["anthropic"]["url"], payload, headers, timeout)


def list_models(provider: str) -> list[str]:
    """What this provider will actually serve today.

    Worth asking rather than remembering. Model names retire quickly:
    gemini-2.5-flash answers "no longer available to new users", and
    OpenRouter's free list is different most months. A picker built from
    memory offers things that 404.
    """
    if provider not in PROVIDERS:
        raise RuntimeError(f"Unknown provider '{provider}'.")

    if provider == "openrouter":
        # Public: no key needed to see the catalogue.
        url, headers = "https://openrouter.ai/api/v1/models", {}
    elif provider == "gemini":
        key = get_key("gemini")
        if not key:
            raise RuntimeError(how_to_get_one("gemini"))
        url, headers = f"{PROVIDERS['gemini']['url']}", {"x-goog-api-key": key}
    else:
        key = get_key(provider)
        if not key:
            raise RuntimeError(how_to_get_one(provider))
        url = PROVIDERS[provider]["url"].replace("/chat/completions", "/models")
        headers = {"Authorization": f"Bearer {key}"}

    request = urllib.request.Request(url, headers={"User-Agent": "Athena/1.0",
                                                   **headers})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code}: {e.read().decode(errors='replace')[:200]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach {provider} ({e.reason})") from None

    if provider == "gemini":
        return sorted(m["name"].replace("models/", "")
                      for m in data.get("models", [])
                      if "generateContent" in m.get("supportedGenerationMethods", []))
    return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))


def look(model: str, image: bytes, mime: str, prompt: str, timeout: int = 60) -> str:
    """Show a Gemini model one image and ask about it. Used to check that a
    diagram found on the web really is the circuit asked for."""
    provider, name = split(model)
    key = get_key(provider) if provider else ""
    if not key:
        raise RuntimeError(how_to_get_one(provider or "gemini"))
    if provider != "gemini":        # OpenAI-shaped vision (Groq's Llama 4)
        data = _post(PROVIDERS[provider]["url"], {"model": name, "temperature": 0, "max_tokens": 300,
                     "messages": [{"role": "user", "content": [
                         {"type": "text", "text": prompt},
                         {"type": "image_url", "image_url": {
                             "url": f"data:{mime};base64," + base64.b64encode(image).decode()}}]}]},
                     {"Authorization": f"Bearer {key}"}, timeout)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            raise RuntimeError("no answer returned") from None
    payload = {"contents": [{"role": "user", "parts": [
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(image).decode()}},
        {"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 4000}}
    data = _post(f"{PROVIDERS['gemini']['url']}/{name}:generateContent", payload,
                 {"x-goog-api-key": key}, timeout)
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise RuntimeError("no answer returned") from None


def chat(model: str, messages: list[dict], temperature: float = 0.1,
         max_tokens: int = 6000, timeout: int = 180) -> str:
    """One turn against a hosted model. Raises RuntimeError with a readable
    reason - a missing key, a rate limit, no network."""
    provider, name = split(model)
    if not provider:
        raise RuntimeError(f"'{model}' is not a hosted model.")
    key = get_key(provider)
    if not key:
        raise RuntimeError(how_to_get_one(provider))

    messages = cloud_safe(messages)
    if provider == "gemini":
        return _gemini(name, key, messages, temperature, max_tokens, timeout)
    if provider == "anthropic":
        data = _anthropic(name, key, messages, None, temperature, max_tokens, timeout)
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return _openai_shape(provider, name, key, messages, temperature,
                         max_tokens, timeout)
