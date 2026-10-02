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

Keys live in data/api_keys.json - never config.yaml, which is the file you
would paste into a chat or commit to a repo. An environment variable
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
    env = PROVIDERS.get(provider, {}).get("env", "")
    return (os.environ.get(env, "") or
            load_json(KEYS_FILE, {}).get(provider, "")).strip()


def set_key(provider: str, key: str) -> None:
    keys = load_json(KEYS_FILE, {})
    keys[provider] = key.strip()
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
    data = _post(PROVIDERS[provider]["url"],
                 {"model": model, "messages": clean,
                  "temperature": temperature, "max_tokens": max_tokens},
                 {"Authorization": f"Bearer {key}"}, timeout)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise RuntimeError("no answer returned") from None


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

    if provider == "gemini":
        return _gemini(name, key, messages, temperature, max_tokens, timeout)
    return _openai_shape(provider, name, key, messages, temperature,
                         max_tokens, timeout)
