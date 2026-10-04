"""Hindi: understood when spoken, answered in Hindi.

Whisper (a multilingual model - small.en knows only English) says which
language each sentence was in. A Hindi command is translated to English
before anything else sees it, so every router, tool and rule works exactly as
for English; the reply is translated back and spoken by a Hindi Kokoro voice.

Translation goes to language.translate_model (Groq's GPT-OSS-120B: about half
a second, and good Hindi), falling back to the local model when offline. The
local 3B model alone was tried first (3 Oct 2026) and is not good enough:
"Done, Chrome is open" came back as "चोरी, चार्ल्स खोला गया" ("theft,
Charles opened"). Only the words of a command addressed to Athena and her
reply are sent - never audio, never speech that was not meant for her.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .config import cfg

# "Athena" as Whisper writes it in Hindi.
# No \b: Devanagari vowel signs are not word characters to `re`, so \b
# matched inside "एथेना" and left the "ा" behind.
_WAKE_HI = re.compile(r"(?:ए|ऐ|अ|आ)(?:थे|थी|थि|ते|टी|टे)न[ाह]*")


def wake_to_latin(text: str) -> str:
    """'एथेना, क्रोम बंद करो' -> 'Athena, क्रोम बंद करो', so the wake-word check works unchanged."""
    return _WAKE_HI.sub("Athena", text)


def _ask(system: str, text: str) -> str:
    model = str(cfg.get("language.translate_model", "groq:openai/gpt-oss-120b"))
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": text}]
    try:
        from . import cloud
        out = cloud.chat(model, msgs, temperature=0.1, max_tokens=600, timeout=8).strip()
        if out:
            return out
    except Exception:
        pass
    from .llm import LLM                                  # offline: the local model
    r = LLM().chat(msgs, max_tokens=200)
    return (r.get("content") if isinstance(r, dict) else getattr(r, "content", str(r))).strip()


@lru_cache(maxsize=256)
def to_english(text: str) -> str:
    """A command in Hindi (or Hinglish) as the English one Athena understands."""
    return _ask("Translate this voice command to English. It is addressed to an assistant named Athena; "
                "keep names of people, apps, songs and courses as they are. Output only the translation.", text) or text


@lru_cache(maxsize=256)
def to_hindi(text: str) -> str:
    """Athena's reply as natural spoken Hindi, all in Devanagari - the Hindi
    voice reads English words in Latin letters badly, so they are written
    as they sound ("क्रोम")."""
    return _ask("Translate to natural, friendly spoken Hindi, as a polite assistant talking to the user (use आप). "
                "Write everything in Devanagari script, including English names of apps, courses and people "
                "- spell them as they sound. Keep numbers as digits. Output only the translation.", text) or text


if __name__ == "__main__":
    assert wake_to_latin("एथेना, क्रोम बंद करो") == "Athena, क्रोम बंद करो"
    assert wake_to_latin("अथीना मेरी अटेंडेंस") .startswith("Athena")
    print(to_english("एथेना, मेरी अटेंडेंस कितनी है"))
    print(to_hindi("Done, Chrome is open."))
