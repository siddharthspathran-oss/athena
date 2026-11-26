"""Which model answers study questions: the choices on offer, the one picked (saved in
data), and building the LLM for it.

Split out of study.py (31 Oct 2026); study.py imports all of it back.
"""
from __future__ import annotations

import time

from .. import cloud
from ..config import DATA, cfg
from ..llm import LLM
from ..store import load_json, save_json


# What the model picker offers. (label, model string, what it needs)
#
# Checked against each provider's live model list rather than written from
# memory - gemini-2.5-flash is already refused for new keys, and DeepSeek and
# Kimi are not among OpenRouter's free models however often they are
# described as free. Anything needing a key you have not added says so in the
# picker instead of failing when you use it.
STUDY_CHOICES: list[tuple[str, str, str]] = [
    ("Gemini 3.5 Flash  (free)", "gemini:gemini-3.5-flash", "gemini"),
    ("Gemini 3.5 Flash-Lite  (free)", "gemini:gemini-3.5-flash-lite", "gemini"),
    ("Qwen 3.8 27B  (free)", "openrouter:qwen/qwen3.8-27b:free", "openrouter"),
    ("GLM 5.2  (free)", "openrouter:z-ai/glm-5.2:free", "openrouter"),
    ("GPT-OSS 120B  (free, Groq - fastest)", "groq:openai/gpt-oss-120b", "groq"),
    ("Qwen 3.8 27B  (free, Groq)", "groq:qwen/qwen3.8-27b", "groq"),
    ("GPT-OSS 20B  (free, Groq)", "groq:openai/gpt-oss-20b", "groq"),
    ("qwen3:8b  (on this PC, slow)", "qwen3:8b", ""),
    ("qwen2.5:7b  (on this PC, quick)", "qwen2.5:7b", ""),
]


# Chosen in the interface rather than the config file, so it survives a
# restart without anyone hand-editing YAML.
_CHOICE_FILE = DATA / "study_model.json"


def choices() -> list[dict]:
    """The picker's options, each marked with whether it can actually run."""
    from .. import cloud

    live = current_model()
    out = []
    for label, model, needs in STUDY_CHOICES:
        ready = not needs or bool(cloud.get_key(needs))
        out.append({"label": label, "model": model, "ready": ready,
                    "needs": needs, "live": model == live})
    return out


def current_model() -> str:
    """The study model in force: the picker's choice, else config.yaml."""
    picked = load_json(_CHOICE_FILE, {}).get("model", "")
    return picked or str(cfg.get("study.model") or "")


def set_model(model: str) -> str:
    """Change the study model, and prove it actually answers.

    Switching used to be a silent write to a file, so a retired model name,
    an expired key or a provider having a bad afternoon only showed up when a
    worksheet came back empty. A two-token question costs a second and turns
    all three into something you are told immediately.
    """
    import time

    from .. import cloud

    known = {m for _, m, _ in STUDY_CHOICES}
    if model and model not in known:
        return f"ERROR: '{model}' is not one of the models on offer."
    save_json(_CHOICE_FILE, {"model": model})

    needs = next((n for _, m, n in STUDY_CHOICES if m == model), "")
    if needs and not cloud.get_key(needs):
        return (f"Switched to {model}, but there is no {needs} key yet.\n"
                f"Run:  python run.py key {needs}")

    hello = [{"role": "user", "content": "Reply with the single word: ready"}]
    # A generous budget for a two-word answer, because a reasoning model
    # spends most of it thinking before it says anything: gpt-oss-120b
    # returned an empty string on 20 tokens and "391" on 2,000. A tight
    # budget here would report a perfectly good model as broken.
    t0 = time.time()
    try:
        if cloud.is_cloud(model):
            reply = cloud.chat(model, hello, temperature=0, max_tokens=2000,
                               timeout=45)
        else:
            msg = LLM(model or None).chat(hello, temperature=0, max_tokens=2000,
                                          think=False)
            reply = getattr(msg, "content", "") or ""
    except Exception as e:
        first = str(e).splitlines()[0][:130]
        return (f"Switched to {model}, but it did not answer: {first}\n"
                f"Pick another, or run:  python run.py models "
                f"{needs or 'ollama'}")

    took = time.time() - t0
    if not reply.strip():
        return f"Switched to {model}, but it replied with nothing."
    return f"Class work now goes to {model} - answered in {took:.1f}s."


def _model() -> LLM:
    """The model that answers class work - `study.model`, or the everyday one.

    A hosted model is not an Ollama model, so the LLM object built here is
    only a carrier; _ask notices the prefix and never calls it.
    """
    configured = current_model() or None
    if configured and cloud.is_cloud(configured):
        return LLM(None)
    return LLM(configured)
