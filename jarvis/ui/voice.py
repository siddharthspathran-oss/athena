"""Voice interface: speak to Athena, it speaks back.

Speech-to-text is faster-whisper running on your RTX 4060.
Text-to-speech is the Windows SAPI voice via pyttsx3, so nothing extra downloads.

Recording is energy-gated: it starts capturing when you speak and stops
after a short silence, so there is no push-to-talk key to hold.
"""
from __future__ import annotations

import queue
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .. import live, offer, presence
from ..agent import Agent
from ..config import cfg
from ..tools import scheduler

SAMPLE_RATE = 16000
BLOCK = 1600          # 100 ms blocks
MIN_SPEECH_S = 0.4    # ignore blips shorter than this
MAX_UTTERANCE_S = 15.0  # hard cap on one recording
CONFIRM_TIMEOUT = 12.0  # how long to wait for a yes/no before giving up

# The on-screen orb, when enabled. A module-level handle keeps the state calls
# to one line at each point rather than threading it through every function.
_ORB = None

# True only once Athena has actually been addressed. The orb stays hidden for
# ordinary noise and conversation, and appears only when it is being spoken to.
_ENGAGED = False


def _engage(on: bool) -> None:
    global _ENGAGED
    _ENGAGED = on
    if not on:
        _orb_state("idle")


_hud_seen = {"t": 0.0, "open": False}


def _hud_open() -> bool:
    """True while the HUD app window is open and not minimised.

    The orb exists to show that Athena is listening when nothing else on
    screen does. With the HUD open it only repeats what the reactor ring is
    already showing, and pops up over the very window you are looking at.

    Matched by its exact title: the app window is called "Athena", while the
    same page in an ordinary Chrome tab is "Athena - Google Chrome", so a
    browser tab does not count. Checked at most once a second.
    """
    now = time.time()
    if now - _hud_seen["t"] < 1.0:
        return _hud_seen["open"]
    found = False
    try:
        import win32gui

        def look(hwnd, _):
            nonlocal found
            if (not found and win32gui.IsWindowVisible(hwnd)
                    and not win32gui.IsIconic(hwnd)
                    and win32gui.GetWindowText(hwnd) == "Athena"
                    and win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin")):
                found = True

        win32gui.EnumWindows(look, None)
    except Exception:
        pass
    _hud_seen.update(t=now, open=found)
    return found


def _hud_hwnd() -> int:
    """The HUD app window's handle, minimised or not; 0 if it is closed."""
    hit = 0
    try:
        import win32gui

        def look(hwnd, _):
            nonlocal hit
            if (not hit and win32gui.IsWindowVisible(hwnd)
                    and win32gui.GetWindowText(hwnd) == "Athena"
                    and win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin")):
                hit = hwnd

        win32gui.EnumWindows(look, None)
    except Exception:
        pass
    return hit


def _show_hud() -> str:
    """Bring the HUD up: restore it if it is only minimised, else open it."""
    hwnd = _hud_hwnd()
    if hwnd:
        try:
            import win32con
            import win32gui

            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return "restored"
    import subprocess
    import sys

    pyw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(pyw if pyw.exists() else sys.executable), "run.py", "gui"],
                     cwd=str(Path(__file__).resolve().parents[2]),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "opened"


_last_wake = {"t": 0.0}


def _on_wake(reason: str) -> None:
    """Bring the HUD up after the lid opens, the PC resumes or you unlock.
    Several of these arrive together for one wake, so only the first in
    20 seconds acts."""
    if not cfg.get("autostart.open_hud_on_wake", True):
        return
    now = time.time()
    if now - _last_wake["t"] < 20:
        return
    _last_wake["t"] = now

    def go():
        time.sleep(2)                # let the desktop come back first
        try:
            live.note(f"{reason} - HUD {_show_hud()}")
        except Exception as e:
            live.note(f"{reason} - could not open the HUD ({e})")
        presence.greet(reason)

    threading.Thread(target=go, daemon=True, name="jarvis-wake-hud").start()


def _watch_wake() -> None:
    """Show the HUD whenever the laptop is opened again.

    Asked for as "open the laptop and Athena is there". Login is covered by
    autostart. The first version of this watched only for the clock jumping,
    which catches a real sleep - but this laptop uses Modern Standby, and on
    25 Sep 2026 closing and opening the lid only switched the screen off and
    on: nothing was frozen, the clock never jumped, the HUD stayed minimised.
    So Windows is asked to say so itself - lid opened, resumed, unlocked - on
    a hidden window, with the clock check kept as a backup. Display-on alone
    is left out on purpose: it also fires when an idle screen is woken by
    the mouse, and the HUD jumping over your work then would be a nuisance.
    """
    threading.Thread(target=_clock_watch, daemon=True, name="jarvis-wake-clock").start()
    try:
        _power_events()
    except Exception as e:
        live.note(f"lid/unlock watch unavailable ({e}) - sleep is still caught")


def _clock_watch() -> None:
    last = time.time()
    while True:
        time.sleep(5)
        now = time.time()
        if now - last > 45:
            _on_wake("laptop woke from sleep")
        last = now


def _power_events() -> None:
    import ctypes
    import uuid
    from ctypes import wintypes

    import win32api
    import win32gui
    import win32ts

    WM_POWERBROADCAST, PBT_RESUME, PBT_SETTING = 0x0218, 0x0012, 0x8013
    WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK = 0x02B1, 0x8
    lid_guid = uuid.UUID("ba3e0f4d-b817-4094-a2d1-d56379e6a0f3")   # GUID_LIDSWITCH_STATE_CHANGE
    # Registering sends the lid's current state straight away ("open"),
    # which is not someone opening it.
    started = time.time()

    def proc(hwnd, msg, wparam, lparam):
        if time.time() - started < 5:
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
        if msg == WM_WTSSESSION_CHANGE and wparam == WTS_SESSION_UNLOCK:
            _on_wake("unlocked")
        elif msg == WM_POWERBROADCAST:
            if wparam == PBT_RESUME:
                _on_wake("laptop resumed")
            elif wparam == PBT_SETTING and lparam:
                # POWERBROADCAST_SETTING: GUID (16 bytes), DWORD length, data.
                raw = ctypes.string_at(lparam, 24)
                if uuid.UUID(bytes_le=raw[:16]) == lid_guid and raw[20] == 1:
                    _on_wake("lid opened")
            return 1
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = proc
    wc.lpszClassName = "AthenaWakeWatch"
    wc.hInstance = win32api.GetModuleHandle(None)
    win32gui.RegisterClass(wc)
    hwnd = win32gui.CreateWindow(wc.lpszClassName, "Athena wake watch", 0, 0, 0, 0, 0,
                                 0, 0, wc.hInstance, None)
    win32ts.WTSRegisterSessionNotification(hwnd, win32ts.NOTIFY_FOR_THIS_SESSION)

    class GUID(ctypes.Structure):
        _fields_ = [("b", ctypes.c_ubyte * 16)]

    g = GUID((ctypes.c_ubyte * 16)(*lid_guid.bytes_le))
    register = ctypes.windll.user32.RegisterPowerSettingNotification
    register.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    register.restype = wintypes.HANDLE
    register(hwnd, ctypes.byref(g), 0)       # 0 = DEVICE_NOTIFY_WINDOW_HANDLE
    win32gui.PumpMessages()


def _orb_state(state: str) -> None:
    live.state(state)               # the HUD, whether or not the orb is on
    if _ORB is not None:
        # Stay off screen while the HUD is open; it shows the same thing.
        _ORB.set_state("idle" if _hud_open() else state)


def _orb_level(rms: float) -> None:
    live.level(rms)
    if _ORB is not None:
        _ORB.set_level(rms)

# Shown to Whisper as context before decoding. Biasing it toward the words we
# actually expect is the cheapest accuracy win available on a small model.
_BASE_BIAS = (
    # Whisper weights whole phrases more heavily than a bare word list, so the
    # awkward names get their own sentences.
    "Athena, open Comet. Athena, close Comet. Athena, open Comet browser. "
    "Athena, open WhatsApp. Athena, open Outlook. Athena, open Spotify and play "
    "music. Athena, close Chrome. Athena, pause the music. Athena, next track. "
    # "close Spotify" came back as "Claude, Spotify", "close, portable" and
    # "close protocol" (25 Sep 2026) - "open Claude" below pulls "close"
    # towards "Claude", so the close commands are said here in full.
    "Athena, close Spotify. Athena, close WhatsApp. Athena, close the music app. "
    "Athena, brief me. Athena, what is my schedule? "
    "Athena, what is my battery at? Athena, take a screenshot. "
    "Athena, set the volume to fifty. Athena, lock my screen. "
    "Athena, find my PDFs. Athena, what is the weather? "
    # Without these, "call" comes back as "oll" or "all" and the command is
    # lost before anything can act on it.
    "Athena, call Riya. Athena, video call Papa. Athena, open Claude. "
    "Athena, send a message saying hello. Athena, set a reminder for ten minutes."
)


def build_prompt_bias() -> str:
    """Prime Whisper with the names of apps actually installed on this PC.

    Without this, a small model rewrites unfamiliar product names into common
    English - "Comet" becomes "comment", "Obsidian" becomes "obsidian" in the
    ordinary sense. Listing the real names makes them available to the decoder.
    Whisper caps the prompt at roughly 224 tokens, so this stays short.
    """
    try:
        from ..tools.system import _start_apps

        names = [n for n, _ in _start_apps()]
    except Exception:
        return _BASE_BIAS

    # Short names are both the most likely to be spoken and the most likely
    # to be mistaken for an ordinary word.
    picks = sorted({n for n in names if len(n) <= 18}, key=lambda n: (len(n), n))[:40]
    bias = _BASE_BIAS
    if picks:
        bias += " Apps installed: " + ", ".join(picks) + "."

    # Contact names matter as much as app names: "Maa" comes back as "Mars"
    # unless the decoder knows the word is expected.
    try:
        from ..tools.messaging import _load as _load_contacts

        people = sorted(_load_contacts())
        if people:
            bias += " Contacts: " + ", ".join(people) + "."
    except Exception:
        pass
    # Games and names not in the Start menu: "Fortnite" came back as "40 point".
    extra = [str(w) for w in (cfg.get("voice.extra_words") or [])]
    if extra:
        bias += " Also: " + ", ".join(extra) + "."
    return bias


PROMPT_BIAS = _BASE_BIAS  # replaced at startup by build_prompt_bias()
MULTILINGUAL = False      # set at startup: a model without .en hears Hindi too
_JUST_NAME = "<athena: name only>"  # _barge_in: the name alone was said while Athena was busy

# Small speech models mangle proper nouns; these are what "Athena" tends to
# come back as. Cheaper and more predictable than lowering the match threshold.
WAKE_ALIASES = {
    "athena", "athena's", "athenas", "athina", "atheena", "athene", "athenna",
    "atena", "athenah", "asena", "athenia", "thena", "athana", "athen",
}


# Words that may come before the wake word without it counting as having been
# said mid-sentence. "Hey Athena" is addressing it; "...and I told Athena" is not.
_WAKE_LEAD_IN = {
    "hey", "hi", "hello", "ok", "okay", "so", "um", "uh", "er", "ah",
    "yo", "please", "and", "now",
}


def _keep_warm() -> None:
    """Pay the one-off costs before the user speaks, then keep them paid.

    The first command otherwise waits for a 5GB model load and a 2 second
    Start-menu scan, so the model is loaded once at start-up. It is no longer
    pinged every ten minutes after that (27 Sep 2026): llm.keep_alive now
    decides when an idle model gives its VRAM back, and "gaming mode" frees
    it on demand - a ping would quietly undo both.
    """
    try:
        from ..tools.system import _start_apps
        _start_apps()
    except Exception:
        pass

    from ..llm import LLM, heal_gpu
    warm = LLM()          # its own client: the main thread is using the other
    ticks = 0
    while True:
        try:
            if ticks == 0:                       # once, at start-up
                # Load the model with the voice command prompt, not a bare
                # "hi": the prompt is then already cached for the first command.
                from ..agent import warm as warm_prompt
                warm_prompt(voice=True, load=True)
            # Every two minutes: is the model actually on the GPU? A server
            # started before the driver was ready stays on the CPU for good,
            # and the first sign of it used to be every reply being slow.
            from .. import gaming
            done = "" if gaming.on() else heal_gpu(warm)
            if done:
                print(f"[gpu] {done}")
                live.note(done)
        except Exception:
            pass
        ticks += 1
        time.sleep(120)


# Openings of things said to Athena, as opposed to things said in a video.
_COMMANDISH = re.compile(
    r"^\W*(?:please\s+|can\s+you\s+|could\s+you\s+|will\s+you\s+)?"
    r"(?:open|close|quit|exit|kill|play|pause|resume|stop|skip|next|previous|mute|unmute|volume|"
    r"turn|set|call|ring|dial|text|message|send|whatsapp|email|mail|remind|add|remove|delete|"
    r"what|what's|whats|who|who's|when|where|how|which|why|is|are|do|does|did|tell|show|give|"
    r"read|look|check|search|find|google|book|reserve|order|start|launch|run|lock|shut|shutdown|"
    r"restart|sleep|take|explain|summari[sz]e|brief|list|write|type|create|make|schedule|go|"
    r"switch|minimi[sz]e|maximi[sz]e|screenshot|note|save|copy|paste|end|hang\s+up|answer|help|"
    r"i\s+need|i\s+want|let's|focus|study|hi|hello|hey|good|can|could|would|will|please|are|am|"
    r"any|thanks?|wake|you\s+there)\b", re.I)


# Words that join a follow-up to what came before: "and tomorrow?", "also
# open it", "okay now play something". Taken off before the command check.
_JOINERS = re.compile(r"^\W*(?:(?:and|also|then|but|so|now|actually|ok|okay|oh|wait|right)\W+)+", re.I)


def _followish(text: str) -> bool:
    """Sounds like the next thing said to Athena, not room talk (9 Oct 2026):
    a command or question once the joining words are off, a follow-up the
    agent understands ("what about DSA", "and tomorrow"), "open it", or a
    sentence Whisper punctuated as a question."""
    from ..agent import _OPEN_IT
    core = _JOINERS.sub("", text)
    return bool(_COMMANDISH.match(core) or _ABOUT.match(text) or _OPEN_IT.match(text)
                or (text.rstrip().endswith("?") and len(text.split()) >= 2))


# Not the agent's own _FOLLOW: its bare "and <anything>" let a lyric through
# ("and then the ocean"); the agent can afford it, the open mic cannot.
_ABOUT = re.compile(r"^\W*(?:and\s+)?(?:what|how)\s+about\s+\S|^\W*(?:and\s+)?same\s+for\s+\S|"
                    r"^\W*(?:and\s+)?(?:for\s+)?(?:today|tomorrow|next\s+week)\W*$", re.I)


_FILLER_ONLY = re.compile(
    r"^\W*(?:yeah|yes|yep|yup|no|nope|okay|ok|oh|ah|um+|uh+|hmm+|right|alright|sure|thanks?(?:\s+you)?|"
    r"so|well|you\s+know|i\s+mean|what|huh|hey|hi|hello)(?:\W+(?:yeah|yes|okay|ok|so|oh|um+|uh+))*\W*$", re.I)


def _trusted_text(segments) -> str:
    """The transcript without the words Whisper invented after you stopped.

    Made-up words come after a pause and with low confidence; what you said
    does not. From the end backwards, a short run of words that follows a
    pause (or a sentence end) is dropped while its confidence is poor.
    """
    words = [w for s in segments for w in (getattr(s, "words", None) or [])]
    if not words:
        return " ".join(s.text for s in segments).strip()
    # Split into runs at pauses and sentence ends.
    runs: list[list] = [[words[0]]]
    for prev, w in zip(words, words[1:]):
        if w.start - prev.end >= 0.45 or prev.word.strip()[-1:] in ".?!":
            runs.append([w])
        else:
            runs[-1].append(w)
    while len(runs) > 1:
        tail = runs[-1]
        conf = sum(w.probability for w in tail) / len(tail)
        if len(tail) <= 4 and conf < float(cfg.get("voice.tail_min_prob", 0.5)):
            runs.pop()
        else:
            break
    return "".join(w.word for r in runs for w in r).strip()


def _echoes_prompt(text: str, segments, seconds: float, prompt: str) -> bool:
    """True when a transcript is Whisper repeating its own primer.

    The primer holds whole commands ("Athena, open Spotify and play music")
    because that is what makes the small model hear the names - and fed a
    cough, a fan or music, Whisper sometimes hands the primer back word for
    word. Spotify then opened with nobody having asked (28 Sep 2026).

    A real command can say the same words, so the words alone decide
    nothing. It is believed unless the decoder itself doubted there was
    speech, or the words came faster than anyone talks.
    """
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if len(words) < 2:
        return False
    primer = " ".join(re.findall(r"[a-z0-9']+", prompt.lower()))
    if " ".join(words) not in primer:
        return False
    segs = list(segments or [])
    no_speech = max((float(getattr(s, "no_speech_prob", 0) or 0) for s in segs), default=0.0)
    logprob = min((float(getattr(s, "avg_logprob", 0) or 0) for s in segs), default=0.0)
    rate = len(words) / max(seconds - float(cfg.get("voice.silence_timeout", 0.65)), 0.3)
    return (no_speech > float(cfg.get("voice.primer_no_speech", 0.3))
            or logprob < float(cfg.get("voice.primer_logprob", -0.7))
            or rate > 4.5)


def _from_last_name(text: str) -> str:
    """ "Athena, play a music of, Athena, play a music of your choice" - you
    started over, so the last "Athena" is where the command begins."""
    hits = [m for m in re.finditer(r"\b[\w']+\b", text)
            if m.group(0).lower() in WAKE_ALIASES]
    if len(hits) > 1:
        return text[hits[-1].start():]
    return text


class _OpenWakeWord:
    """openWakeWord behind the same two calls as Porcupine (frame_length,
    process), so wait_for_wake works with either. Free, local, no account;
    the "athena" model is trained once in openWakeWord's Colab notebook."""
    frame_length = 1280                     # 80 ms at 16 kHz

    def __init__(self, path, threshold: float):
        from openwakeword.model import Model
        from openwakeword.utils import download_models
        # A path is a model trained for "Athena"; a bare name ("hey_jarvis")
        # is one of openWakeWord's ready-made ones, fetched once.
        builtin = not str(path).endswith((".onnx", ".tflite"))
        download_models(model_names=[str(path)] if builtin else [])
        self.m = Model(wakeword_models=[str(path)],
                       inference_framework="onnx" if builtin or str(path).endswith(".onnx") else "tflite")
        self.threshold = threshold

    def process(self, frame) -> int:
        scores = self.m.predict(frame)
        if max(scores.values(), default=0) >= self.threshold:
            self.m.reset()                  # no double trigger on the next frames
            return 0
        return -1


def _porcupine():
    """A dedicated wake-word engine, if one is set up. None otherwise - then
    Whisper listens for the name as before.

      openWakeWord: data/athena.onnx (or .tflite), trained in its Colab
      notebook. Free, no account - used first.
      Porcupine: a Picovoice AccessKey in data/api_keys.json under
      "picovoice" plus data/athena_windows.ppn from console.picovoice.ai.
    """
    if cfg.get("voice.wake_engine", "auto") == "whisper":
        return None
    from ..cloud import get_key
    from ..config import DATA
    ready = str(cfg.get("voice.wake_model", "") or "").strip()
    if ready and not (DATA / "athena.onnx").exists():
        # No "Athena" model trained yet: a ready-made phrase ("hey_jarvis",
        # "alexa", "hey_mycroft") gives the dedicated detector today.
        try:
            eng = _OpenWakeWord(ready, float(cfg.get("voice.wake_threshold", 0.5)))
            print(f"[wake word: openWakeWord listening for '{ready.replace('_', ' ')}' - Whisper only runs after it]")
            return eng
        except Exception as e:
            print(f"[wake word: {ready} failed ({str(e)[:120]}) - Whisper listens for the name]")
    for name in ("athena.onnx", "athena.tflite"):
        if (DATA / name).exists():
            try:
                eng = _OpenWakeWord(DATA / name, float(cfg.get("voice.wake_threshold", 0.5)))
                print(f"[wake word: openWakeWord listening for the name ({name}) - Whisper only runs after it]")
                return eng
            except Exception as e:
                print(f"[wake word: openWakeWord failed ({str(e)[:120]}) - Whisper listens for the name]")
                return None
    key = get_key("picovoice")
    ppn = DATA / cfg.get("voice.wake_keyword_file", "athena_windows.ppn")
    if not key or not ppn.exists():
        return None
    try:
        import pvporcupine
        pp = pvporcupine.create(access_key=key, keyword_paths=[str(ppn)],
                                sensitivities=[float(cfg.get("voice.wake_sensitivity", 0.6))])
        print("[wake word: Porcupine listening for the name - Whisper only runs after it]")
        return pp
    except Exception as e:
        print(f"[wake word: Porcupine failed ({str(e)[:120]}) - Whisper listens for the name]")
        return None


def _wake_probability(segments) -> float | None:
    """Whisper's confidence in the first wake-word-like word, or None."""
    for seg in segments:
        for w in (getattr(seg, "words", None) or []):
            word = re.sub(r"[^a-z']", "", w.word.lower())
            if word in WAKE_ALIASES:
                return float(w.probability)
    return None


def _strip_wake(text: str, wake: str, at_start: bool = True) -> tuple[bool, str]:
    """Detect the wake word and return (heard_it, the rest of the sentence).

    Matches exactly, against known mishearings, and finally by fuzzy ratio,
    so a slightly garbled wake word still gets through.

    at_start: require the wake word to open the sentence. Accepting it
        anywhere meant ordinary conversation that merely mentioned Athena was
        taken as a command. A few filler words may still precede it.
    """
    from difflib import SequenceMatcher

    def is_wake(w: str) -> bool:
        # Fuzzy only for words that start like the name and are long enough:
        # "then" is inside "a-THEN-a" and scored 0.8, so "And then turn the
        # text..." woke Athena up (26 Sep 2026).
        return (w == wake or w in WAKE_ALIASES
                or (len(w) >= 5 and w[:1] == wake[:1]
                    and SequenceMatcher(None, w, wake).ratio() >= 0.72))

    def scan(chunk: str) -> tuple[bool, str]:
        words = chunk.split()
        for i, raw in enumerate(words):
            w = raw.lower().strip(",.!?'\"-")
            if not w:
                continue
            if is_wake(w):
                return True, " ".join(words[i + 1:]).lstrip(" ,.!?'-")
            # Real words before the wake word mean it was mentioned, not used.
            if at_start and w not in _WAKE_LEAD_IN:
                return False, chunk
        return False, chunk

    if not at_start:
        return scan(text)

    # "At the start" means the start of a sentence, not of the recording. With
    # a TV or music in the room a recording never ends on a pause - it runs to
    # the 15-second cap - so "Athena, what time is it" arrives after whatever
    # else was said, and requiring it to open the whole transcript threw it
    # away every time. A sentence of its own still has to begin with it, so
    # "...and I told Athena about it" is still not a command.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    for n, sentence in enumerate(sentences):
        hit, rest = scan(sentence)
        if hit:
            tail = " ".join(sentences[n + 1:])
            return True, (rest + " " + tail).strip()
    return False, text


def _for_speech(text: str) -> str:
    """Written text as it should be said: file names and symbols a neural
    voice would otherwise spell out or trip over."""
    import re as _re

    t = _re.sub(r"\.pdf\b", " PDF", text, flags=_re.I)          # "notes PDF"
    t = _re.sub(r"\.(?:docx?|pptx?|xlsx?|txt|png|jpe?g|mp4|zip)\b", "", t, flags=_re.I)
    t = t.replace("_", " ").replace("&", " and ").replace("%", " percent")
    t = _re.sub(r"\band/or\b", "and or", t, flags=_re.I)
    t = _re.sub(r"(?<=\w)/(?=\w)", " or ", t)
    t = _re.sub(r"\s+[-–—]+\s+", ", ", t)      # a spoken dash is a pause; Piper ran straight through it
    return _re.sub(r"\s+", " ", t).strip()


def _breathe(audio, text: str, rate: int):
    """The pause a person leaves after a piece of speech: longer after a full
    stop than after a comma. Sentences used to be played back to back with
    no gap at all, which is what made Athena sound read out (3 Oct 2026)."""
    import numpy as np
    end = text.rstrip()[-1:] if text.strip() else ""
    gap = float(cfg.get("voice.sentence_pause", 0.32)) if end in ".!?।" else 0.18 if end in ",;:-–" else 0.12
    return np.concatenate([audio, np.zeros(int(gap * rate), dtype=audio.dtype)])


class Speaker:
    """Text-to-speech on its own thread so speaking never blocks listening.

    Talks to SAPI through COM directly rather than via pyttsx3. pyttsx3's
    runAndWait() deadlocks when called repeatedly from a background thread on
    SAPI5 - it hangs inside COM with every thread waiting, taking the whole
    assistant down with it. Creating the voice object inside this thread, with
    its own COM apartment, avoids that entirely.
    """

    def __init__(self) -> None:
        self.q: queue.Queue[str | None] = queue.Queue()
        self._speaking = threading.Event()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self.current = ""            # what is being said now - for echo checks
        self.started: list[float] = []   # when each piece began playing - for timings
        self._backend = "none"
        # The neural voice (jarvis/tts.py), once loaded: this thread turns text
        # into audio while a player thread speaks the previous sentence, so
        # there is no gap between them. Every stop() bumps _gen, and anything
        # made before it is dropped rather than played.
        self._neural = None
        self._gen = 0
        self._synthing = threading.Event()
        self._neural_loading = threading.Event()
        self._play_q: queue.Queue = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()
        self._ready.wait(timeout=10)

    def _make_voice(self):
        try:
            import comtypes.client

            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            # Speaking is dead time on every single command, so the rate is
            # worth a little more than the default.
            voice.Rate = int(cfg.get("voice.tts_rate", 2))

            want = (cfg.get("voice.tts_voice", "") or "").strip().lower()
            if want:
                for token in voice.GetVoices():
                    try:
                        if want in token.GetDescription().lower():
                            voice.Voice = token
                            break
                    except Exception:
                        continue
            self._backend = "sapi"
            return voice
        except Exception as e:
            print(f"[sapi unavailable: {e}]")
            return None

    def _loop(self) -> None:
        voice = None
        try:
            import comtypes

            comtypes.CoInitialize()
            voice = self._make_voice()
        except Exception as e:
            print(f"[tts init failed: {e} - answers will be printed only]")
        self._ready.set()

        # The cloned voice, if set up; loaded here so the first reply does not
        # pay for it. Any failure leaves the Windows voice in charge.
        clone = False
        if cfg.get("voice.engine", "sapi") == "clone":
            try:
                from .. import cloned_voice
                if cloned_voice.available():
                    where = cloned_voice.load()
                    clone = True
                    print(f"[voice: cloned voice ready on the {where.upper()}]")
                else:
                    print("[voice: cloned voice not set up - using the Windows voice]")
            except Exception as e:
                print(f"[voice: cloned voice failed ({e}) - using the Windows voice]")
        else:
            # Loaded in the background: until it is ready (a few seconds at
            # start-up), the Windows voice speaks, so nothing waits on it.
            self._neural_loading.set()
            threading.Thread(target=self._load_neural, daemon=True, name="tts-load").start()

        while True:
            text = self.q.get()
            if text is None:
                return
            hindi = isinstance(text, tuple)              # (text, "hi"): reply in Hindi
            if hindi:
                text = text[0]
            # The neural voice still loading: wait for it, rather than say the
            # first sentence (the welcome) in the Windows voice and switch
            # after - "why does Athena change her voice" (4 Oct 2026).
            if self._neural is None and self._neural_loading.is_set():
                self._synthing.set()                 # busy meanwhile: not a gap to listen in
                deadline = time.time() + float(cfg.get("voice.wait_for_voice", 20))
                while self._neural is None and self._neural_loading.is_set() and time.time() < deadline:
                    time.sleep(0.1)
                if self._neural is None:
                    self._synthing.clear()
            if self._neural is not None:
                gen = self._gen
                self._synthing.set()
                try:
                    if hindi and getattr(self._neural, "speaks_hindi", False):
                        from .. import hindi as _hi
                        text = _hi.to_hindi(text)
                        audio = self._neural.synth(text, hindi=True)
                    elif getattr(self._neural, "speaks_hindi", False):
                        # Kokoro: each sentence in its own tone - "Welcome back"
                        # bright, "it's still overdue" softer, a question lifted.
                        from .. import tts as _tts
                        parts = [p for p in re.split(r"(?<=[.!?])\s+", _for_speech(text)) if p.strip()] or [text]
                        pieces = []
                        for n, part in enumerate(parts):
                            a = self._neural.synth(part, mood=_tts.mood_of(part))
                            pieces.append(_breathe(a, part, self._neural.sample_rate) if n < len(parts) - 1 else a)
                        audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
                    else:
                        audio = self._neural.synth(_for_speech(text))
                    if len(audio):
                        audio = _breathe(audio, text, self._neural.sample_rate)
                    if gen == self._gen and len(audio):
                        self._play_q.put((gen, text, audio, self._neural.sample_rate))
                    continue
                except Exception as e:
                    print(f"[{self._neural.name} failed: {str(e)[:100]} - Windows voice for this one]")
                finally:
                    self._synthing.clear()
            if clone:
                self._speaking.set()
                try:
                    from .. import cloned_voice
                    cloned_voice.speak(text)
                    continue
                except Exception as e:
                    print(f"[cloned voice failed: {e} - Windows voice for this one]")
                finally:
                    self._speaking.clear()
            if voice is None:
                continue
            self._speaking.set()
            self._stop.clear()
            self.current = text
            self.started.append(time.time())
            try:
                # Asynchronous, so it can be cut off: "Athena, stop" in the
                # middle of a long answer used to wait for the whole thing.
                voice.Speak(text, 1)                       # SVSFlagsAsync
                while not voice.WaitUntilDone(80):
                    if self._stop.is_set():
                        voice.Speak("", 3)                 # async + purge: silence now
                        break
            except Exception:
                # A dead COM object should not silence the assistant forever.
                try:
                    voice = self._make_voice()
                except Exception:
                    voice = None
            finally:
                self._speaking.clear()

    def _load_neural(self) -> None:
        try:
            from .. import tts
            engine = tts.load()
        except Exception as e:
            print(f"[voice: neural voice unavailable ({str(e)[:100]}) - Windows voice]")
            engine = None
        if engine is not None:
            threading.Thread(target=self._play_loop, daemon=True, name="tts-play").start()
            self._neural = engine
            self._backend = engine.name
        self._neural_loading.clear()

    def _play_loop(self) -> None:
        import sounddevice as sd

        while True:
            gen, text, audio, rate = self._play_q.get()
            if gen != self._gen:
                continue                            # made before a stop: never play it
            self._speaking.set()
            self.current = text
            self.started.append(time.time())
            try:
                from .. import echo as _echo_mod
                _echo_mod.guard.played(audio, rate, start=time.monotonic())   # so its echo is known
                sd.play(audio, rate)
                end = time.time() + len(audio) / rate + 0.05
                while time.time() < end:
                    if gen != self._gen:
                        sd.stop()                   # "Athena, stop": silence now
                        break
                    time.sleep(0.03)
            except Exception as e:
                print(f"[voice playback failed: {str(e)[:100]}]")
            finally:
                if self._play_q.empty():
                    self._speaking.clear()

    def say(self, text: str, hindi: bool = False) -> None:
        # Strip markdown noise that sounds wrong when read aloud, and the
        # [share:<id>] marker the phone's chat turns into a Download button.
        text = re.sub(r"\s*\[share:[\w-]+\]", "", text)
        clean = text.replace("*", "").replace("`", "").replace("#", "")
        print(f"athena > {text}" + ("   (in Hindi)" if hindi else ""))
        self.q.put((clean[:600], "hi") if hindi else clean[:600])

    def busy(self) -> bool:
        """True while there is still speech queued, being made, or playing."""
        return (not self.q.empty() or self._speaking.is_set()
                or self._synthing.is_set() or not self._play_q.empty())

    def stop(self) -> None:
        """Stop talking now and drop anything still queued."""
        self._gen += 1
        for q in (self.q, self._play_q):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
        self._speaking.clear()
        self._stop.set()

    def wait_until_done(self, timeout: float = 20.0) -> bool:
        """Block until speech finishes, but never longer than timeout.

        Returns False if it gave up. Nothing in a voice assistant should wait
        on another component without a bound - that is how one stuck part
        freezes everything.
        """
        deadline = time.time() + timeout
        while self.busy():
            if time.time() > deadline:
                return False
            time.sleep(0.05)
        return True


# Fed music or noise instead of speech, Whisper invents subtitle credits from
# its training data. These are never real commands, so drop them silently.
_HALLUCINATIONS = (
    "subs by", "subtitles by", "subtitled by", "amara.org", "thanks for watching",
    "thank you for watching", "please subscribe", "like and subscribe",
    "transcription by", "captions by", "www.", ".com", ".org", ".co.uk",
)


# Hesitation noises and throwaway fragments. Treating these as commands is
# what makes an assistant feel like it is butting into your conversation.
_FILLER = {
    "", "you", "thank you", "thanks", "bye", "oh", "uh", "um", "umm", "uhh",
    "hmm", "hm", "mm", "mhm", "ah", "ahh", "eh", "er", "huh", "yeah", "yep",
    "ok", "okay", "right", "so", "well", "like", "hey", "hi", "what",
}


def fix_mishearings(text: str) -> str:
    """Repair substitutions Whisper makes consistently.

    "Open" at the start of a command comes back as "1", "One" or "Won" over
    and over - the leading plosive is quiet and the word is short. Without a
    verb the model has nothing to act on, so the command dies. Only the first
    word is touched, and only for these few high-confidence cases.
    """
    import re

    # Not before a number word - "one hundred and two" is a quantity, not a
    # command - and only when something follows that could be opened.
    return re.sub(
        r"^\s*(1|one|won|wan|opan|ope)\b[\s,.]*"
        r"(?!hundred|thousand|million|billion|dozen|percent|degrees?|"
        r"minutes?|seconds?|hours?|days?|weeks?|months?|years?|"
        r"o'?clock|o\b|am\b|pm\b|and\b|of\b|$)"
        r"(?=\S)",
        "open ", text, count=1, flags=re.IGNORECASE)


def _squash(word: str) -> set[str]:
    """Variants of a word with repeated letters collapsed.

    Whisper writes hesitations as it hears them - "hmmm", "ahhh", "ummmm" -
    so a fixed list never matches. Collapsing runs of letters catches them all.
    """
    import re

    return {word,
            re.sub(r"(.)\1{2,}", r"\1\1", word),
            re.sub(r"(.)\1+", r"\1", word)}


def is_hallucination(text: str) -> bool:
    """True if a transcript is noise, a Whisper artefact, or mere filler."""
    low = text.strip().lower()
    if not low:
        return True
    if any(h in low for h in _HALLUCINATIONS):
        return True

    stripped = low.strip(" .,!?-'\"")
    if _squash(stripped) & _FILLER:
        return True

    words = [w.strip(" .,!?-'\"") for w in stripped.split()]
    words = [w for w in words if w]
    if not words:
        return True

    # A single short word is never a real instruction.
    if len(words) == 1 and len(words[0]) <= 3:
        return True

    # Every word is a hesitation noise, e.g. "ahhh, umm, hmm".
    if all(_squash(w) & _FILLER for w in words):
        return True

    # Whisper loops on non-speech: "buzz, buzz, buzz, buzz, buzz".
    if len(words) >= 4 and len(set(words)) <= 2:
        return True

    return False


_EARCON = {"path": None}


def earcon() -> None:
    """A soft two-note tone the moment Athena knows she is being spoken to.

    Assistants feel instant because something answers at once, long before
    the answer does. Played by Windows from a small WAV file, asynchronously
    and on its own, so it never waits and never cuts off Athena's voice
    (sounddevice has one shared output stream; winsound does not use it).
    """
    if not cfg.get("voice.earcon", True):
        return
    try:
        import winsound

        if _EARCON["path"] is None:
            import wave

            from ..config import DATA
            rate = 22050
            notes = []
            for freq, dur in ((880, 0.07), (1320, 0.09)):
                t = np.arange(int(rate * dur)) / rate
                env = np.minimum(1, np.minimum(t / 0.008, (dur - t) / 0.03))   # no clicks
                notes.append(0.18 * np.sin(2 * np.pi * freq * t) * env)
            pcm = (np.concatenate(notes) * 32767).astype(np.int16)
            path = DATA / "earcon.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm.tobytes())
            _EARCON["path"] = str(path)
        winsound.PlaySound(_EARCON["path"], winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)
    except Exception:
        pass


def chime(kind: str = "ask") -> None:
    """A short tone so a spoken question is unmistakably a question.

    Speech alone is easy to talk over or half-hear; a rising pair of tones
    reads as "I need an answer" even from across the room.
    """
    try:
        import winsound

        if kind == "ask":
            winsound.Beep(660, 120)
            winsound.Beep(880, 160)
        elif kind == "ok":
            winsound.Beep(880, 110)
        else:  # cancelled or timed out
            winsound.Beep(500, 130)
            winsound.Beep(380, 190)
    except Exception:
        pass


def _describe_action(tool_name: str, args: dict) -> str:
    """A short spoken summary of what is about to happen."""
    if tool_name == "whatsapp_message":
        return f"I'll send {args.get('to', 'them')} the message: {args.get('text', '')}"
    if tool_name == "close_app":
        return f"I'll close {args.get('name', 'that app')}"
    if tool_name == "delete_file":
        return f"I'll move {args.get('path', 'that file')} to the recycle bin"
    if tool_name == "power_action":
        return f"I'll {args.get('action', 'do that')} the PC"
    if tool_name == "run_powershell":
        return f"I'll run the command: {args.get('command', '')}"
    detail = ", ".join(f"{k} {v}" for k, v in list(args.items())[:2])
    return f"I'll run {tool_name.replace('_', ' ')}" + (f" with {detail}" if detail else "")


class Listener:
    """Captures one utterance at a time from the default microphone."""

    def __init__(self) -> None:
        import sounddevice as sd

        self.sd = sd
        # Bounded: a minute of audio. Nothing reads while a command runs
        # without a barge-in listen, and stale sound must not pile up.
        self.blocks: queue.Queue[np.ndarray] = queue.Queue(maxsize=600)
        self._stream = None
        self.last_wait = 0.0      # how long the last sentence waited for its end
        self.floor = 0.0          # the room's quiet level, learned as it listens

    def _callback(self, indata, _frames, _t, status) -> None:
        try:
            self.blocks.put_nowait(indata.copy())
        except queue.Full:
            try:
                self.blocks.get_nowait()          # drop the oldest
                self.blocks.put_nowait(indata.copy())
            except (queue.Empty, queue.Full):
                pass

    def _ensure_stream(self) -> None:
        """Open the microphone once and leave it open.

        It used to be opened and closed around every recording - and while
        Athena talks and listens for "stop", that is every second or so. Each
        open switches this laptop's Realtek driver into its capture mode and
        glitched the speech playing at the same moment: Athena's voice
        stuttered (25 Sep 2026). One long-lived stream has no such moments.
        """
        if self._stream is not None and self._stream.active:
            return
        self._close_stream()
        self._stream = self.sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                           dtype="float32", blocksize=BLOCK,
                                           callback=self._callback)
        self._stream.start()

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def drain(self) -> None:
        """Throw away buffered audio.

        Called after Athena speaks so it does not transcribe its own voice.
        """
        while True:
            try:
                self.blocks.get_nowait()
            except queue.Empty:
                return

    def wait_for_wake(self, pp) -> str:
        """Block until the wake-word engine hears the name ("word") or the
        HUD's mic button is pressed ("tap"). Whisper does not run meanwhile:
        songs, videos and talk in the room are never transcribed at all."""
        self._ensure_stream()
        buf = np.zeros(0, dtype=np.int16)
        since = time.time()
        next_tap_check = 0.0
        while True:
            try:
                block = self.blocks.get(timeout=1.0)
            except queue.Empty:
                self._ensure_stream()
                continue
            _orb_level(float(np.sqrt(np.mean(block ** 2))))
            buf = np.concatenate([buf, (np.clip(block.flatten(), -1, 1) * 32767).astype(np.int16)])
            while len(buf) >= pp.frame_length:
                frame, buf = buf[:pp.frame_length], buf[pp.frame_length:]
                if pp.process(frame) >= 0:
                    return "word"
            if time.time() > next_tap_check:
                next_tap_check = time.time() + 0.5
                try:
                    if live.WAKE_FILE.stat().st_mtime > since:
                        return "tap"
                except OSError:
                    pass

    def record_utterance(self, silence_timeout: float,
                         max_wait: float | None = None,
                         cap: float | None = None,
                         until=None) -> np.ndarray | None:
        """Wait for speech and return it as float32 audio.

        max_wait bounds how long to wait for someone to START speaking. Without
        it this blocks forever, which deadlocks the whole assistant if it asks
        a question nobody answers. None means wait indefinitely, which is only
        correct for the idle listening loop.

        Two ways a microphone dies without raising anything, and both left
        Athena sitting there looking idle while it could not hear a word:

          * the stream stalls - no blocks at all. A headset connecting or
            disconnecting does it, and the stream never came back without a
            restart. After a few empty seconds the stream is reopened.

        Pure digital silence is deliberately NOT treated as a fault: this
        laptop's microphone array suppresses noise and outputs exact zeros
        between sounds, so a quiet room looks identical to a muted mic.
        """
        started_waiting = time.time()
        while True:
            result, reopen = self._record_once(silence_timeout, max_wait,
                                               started_waiting, cap, until)
            if not reopen:
                return result
            print("[mic] no audio for 4s - reopening the microphone")
            live.note("mic stalled - reopening")
            # PortAudio lists devices once, at start-up. After a headset comes
            # or goes, reopening against that stale list finds the same dead
            # device again, so the list is rebuilt first.
            self._close_stream()
            try:
                self.sd._terminate()
                self.sd._initialize()
            except Exception:
                pass
            time.sleep(0.5)

    def _record_once(self, silence_timeout: float, max_wait: float | None,
                     started_waiting: float, cap: float | None = None,
                     until=None):
        """One open microphone stream. Returns (audio or None, reopen?)."""
        captured: list[np.ndarray] = []
        from collections import deque
        preroll: deque = deque(maxlen=9)       # 0.8 s before speech (0.3 s still clipped "A-the-"), plus the trigger block
        speaking = False
        last_voice = time.time()
        stalls = 0
        vad = _vad() if cfg.get("voice.vad", True) else None
        vad_silence = float(cfg.get("voice.vad_silence", 0.45))
        heard_speech = False
        next_check = 0

        self._ensure_stream()
        while True:
            if max_wait is not None and not speaking:
                if time.time() - started_waiting > max_wait:
                    return None, False
            if until is not None and not speaking and until():
                return None, False
            try:
                block = self.blocks.get(timeout=1.0)
                stalls = 0
            except queue.Empty:
                stalls += 1
                if stalls >= 4:
                    self._close_stream()
                    return None, True
                continue

            rms = float(np.sqrt(np.mean(block ** 2)))
            _orb_level(rms)
            # The threshold follows the room: a little above its own quiet, not
            # a fixed 0.012 - two metres away you peaked at 0.02 and mostly sat
            # under it (4 Oct 2026). Never below voice.silence_rms.
            gate = max(float(cfg.get("voice.silence_rms", 0.006)), self.floor * float(cfg.get("voice.gate_ratio", 2.5)))
            if not speaking:
                preroll.append(block)
                # Quick to fall, slow to rise: a word or a door slam barely
                # moves it. Learning only below the threshold, it never learnt
                # a room louder than the starting threshold.
                self.floor = (rms if self.floor == 0 else
                              0.9 * self.floor + 0.1 * rms if rms < self.floor else
                              0.995 * self.floor + 0.005 * rms)
            if rms > gate:
                if not speaking:
                    speaking = True
                    # The 0.8 s before it got loud: the soft start of "Athena"
                    # was cut off, and Whisper heard "You now" (4 Oct 2026).
                    captured = list(preroll)[:-1]
                    preroll.clear()
                    heard_speech, next_check = False, 0
                last_voice = time.time()
                captured.append(block)

                # Continuous sound - music, a fan, a TV - never drops below
                # the silence threshold, so without a hard cap this records
                # forever and the assistant appears frozen.
                if len(captured) * BLOCK / SAMPLE_RATE >= (cap or MAX_UTTERANCE_S):
                    audio = np.concatenate(captured).flatten()
                    if vad is not None and not heard_speech and not _has_speech(vad, audio):
                        speaking, captured = False, []
                        continue                      # 15 s of music: nothing said
                    self.last_wait = 0.0
                    return audio, False
            elif speaking:
                captured.append(block)
                if time.time() - last_voice > silence_timeout:
                    audio = np.concatenate(captured).flatten()
                    if len(audio) / SAMPLE_RATE < MIN_SPEECH_S:
                        speaking = False
                        continue
                    # A cough, a door, a keyboard: loud but not a voice. Not
                    # sent to Whisper at all - that is where "subtitles by"
                    # and a repeated primer come from.
                    if vad is not None and not heard_speech and not _has_speech(vad, audio):
                        speaking, captured = False, []
                        continue
                    self.last_wait = silence_timeout
                    return audio, False

            # The speech detector ends a sentence sooner than the loudness
            # rule - a voice stopping, not the room going quiet - and ends it
            # at all in a room that never goes quiet (music, a fan).
            if vad is not None and speaking and len(captured) >= next_check:
                next_check = len(captured) + 1              # every 100 ms block
                window = np.concatenate(captured[-12:]).flatten()   # last 1.2 s
                gap = _silence_after_speech(vad, window)
                if gap is not None:
                    heard_speech = True
                    if gap >= vad_silence and len(captured) * BLOCK / SAMPLE_RATE >= MIN_SPEECH_S:
                        self.last_wait = gap
                        return np.concatenate(captured).flatten(), False

# ---------------------------------------------------------------- speech detector
# Silero VAD, as shipped inside faster-whisper - no extra package. It tells a
# voice from a fan, music, a cough or a keyboard, which a loudness threshold
# cannot, so a sentence ends when the voice stops (about 0.45 s, instead of
# 0.65 s of quiet), and noise never reaches Whisper at all.

_VAD = {"fn": None, "tried": False}


def _vad():
    if not _VAD["tried"]:
        _VAD["tried"] = True
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps

            opts = VadOptions(threshold=float(cfg.get("voice.vad_threshold", 0.5)),
                              min_silence_duration_ms=100, speech_pad_ms=0)
            get_speech_timestamps(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), opts)   # load it now
            _VAD["fn"] = lambda audio: get_speech_timestamps(audio, opts)
        except Exception as e:
            print(f"[speech detector unavailable ({str(e)[:80]}) - ending sentences on loudness]")
    return _VAD["fn"]


def _silence_after_speech(vad, audio: np.ndarray) -> float | None:
    """Seconds since speech last ended in `audio`, or None if it holds none."""
    try:
        spans = vad(audio.astype(np.float32))
    except Exception:
        return None
    if not spans:
        return None
    return (len(audio) - spans[-1]["end"]) / SAMPLE_RATE


def _has_speech(vad, audio: np.ndarray) -> bool:
    try:
        return bool(vad(audio.astype(np.float32)))
    except Exception:
        return True                       # unsure: let Whisper decide, as before


def _register_cuda_dlls() -> None:
    """Let ctranslate2 find the CUDA libraries it needs.

    GPU Whisper wants cuBLAS and cuDNN. Ollama bundles cuBLAS but not cuDNN,
    which is why pointing only at Ollama's folder left every spoken command
    being transcribed on the CPU beside an idle GPU.

    Both come from pip as nvidia-cublas-cu12 and nvidia-cudnn-cu12, but they
    land inside site-packages, and Windows does not search there for DLLs -
    installing them changes nothing on its own. They have to be named here.
    """
    import os
    import site

    roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
             / "lib" / "ollama" / sub for sub in ("cuda_v12", "cuda_v13")]

    packages = list(site.getsitepackages())
    user_site = site.getusersitepackages()
    packages += [user_site] if isinstance(user_site, str) else list(user_site)
    for base in packages:
        nvidia = Path(base) / "nvidia"
        if nvidia.is_dir():
            roots += sorted(nvidia.glob("*/bin"))

    added = []
    for d in roots:
        if d.is_dir():
            added.append(str(d))
            try:
                os.add_dll_directory(str(d))
            except (OSError, AttributeError):
                pass
    # add_dll_directory covers DLLs loaded with the newer search flags;
    # a dependency pulled in by an already-loaded extension still goes
    # through PATH, so set both.
    if added:
        os.environ["PATH"] = os.pathsep.join(added + [os.environ.get("PATH", "")])


def _load_whisper(model_size: str, device: str):
    """Build a Whisper model and prove it actually runs.

    The GPU path can construct fine and only fail on the first transcribe
    (a missing cuDNN surfaces there, not at load), so this runs a throwaway
    transcription against silence before committing to the device.
    """
    # Before the import, not after. ctranslate2 resolves cuBLAS and cuDNN when
    # its extension module loads, so a directory added afterwards is never
    # consulted - the search has already failed and the CPU fallback is
    # already decided. This ran in the wrong order and cost every spoken
    # command a CPU transcription.
    _register_cuda_dlls()

    from faster_whisper import WhisperModel

    def _build(dev: str, compute: str):
        model = WhisperModel(model_size, device=dev, compute_type=compute)
        probe = np.zeros(SAMPLE_RATE // 2, dtype=np.float32)
        list(model.transcribe(probe, language="en", beam_size=1)[0])
        return model

    if device in ("cuda", "auto"):
        # A few tries before settling for the CPU. On 3 Oct 2026 Ollama updated
        # and restarted while voice was starting; cuBLAS failed once with
        # INVALID_VALUE, and every command after that took 5 s to hear instead
        # of 0.5. A moment later the same GPU load works.
        reason = ""
        for attempt, compute in enumerate([cfg.get("voice.whisper_compute", "float16")] * 3 + ["int8_float16"]):
            if attempt:
                time.sleep(5)
            try:
                # int8_float16: int8 weights, fp16 maths - about half the VRAM of
                # float16 at practically the same accuracy and speed.
                model = _build("cuda", compute)
                print(f"Whisper ({model_size}) running on the GPU.")
                return model
            except Exception as e:
                reason = str(e).split("\n")[0][:120]
        print(f"GPU unavailable for Whisper ({reason})")
        print("Falling back to CPU - for base.en this is still comfortably fast.")

    model = _build("cpu", "int8")
    print(f"Whisper ({model_size}) running on the CPU.")
    return model


def _level(audio: np.ndarray) -> np.ndarray:
    """Quiet speech brought up to a normal level before Whisper. From two
    metres you peaked at 0.02 against 0.06 at the desk (4 Oct 2026) - too
    faint for a confident "Athena". At most voice.max_gain times, so a near
    silent recording is not turned into loud noise."""
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak < 1e-4:
        return audio
    gain = min(float(cfg.get("voice.max_gain", 8.0)), 0.5 / peak)
    return (audio * gain).astype(np.float32) if gain > 1 else audio


def _start_wake_shadow() -> None:
    """The silent test of the trained wake word (finetune/wake_shadow.py),
    started with Athena so a shutdown does not end it. voice.wake_shadow:
    false once the test is over."""
    script = Path(__file__).resolve().parents[3] / "finetune" / "wake_shadow.py"
    if not cfg.get("voice.wake_shadow", False) or not script.exists():
        return
    try:
        import subprocess

        import psutil
        # Per process: one process whose details can't be read used to end the
        # whole check, and every restart started another copy (5 running, 4 Oct 2026).
        for p in psutil.process_iter(["cmdline"]):
            try:
                cmd = p.info["cmdline"] or []
                if cmd and cmd[-1].endswith("wake_shadow.py"):
                    return
            except Exception:
                continue
        subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")), str(script)],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        print(f"[wake test not started: {e}]")


def _voice_loop() -> None:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        print("ERROR: the voice interface needs faster-whisper and sounddevice.")
        print("Run: pip install faster-whisper sounddevice pyttsx3")
        return

    live.start_heartbeat()
    live.state("idle")
    model_size = cfg.get("voice.whisper_model", "base.en")
    device = cfg.get("voice.device", "auto")
    wake = (cfg.get("voice.wake_word", "") or "").lower().strip()
    wake_at_start = bool(cfg.get("voice.wake_at_start", True))
    timeout = float(cfg.get("voice.silence_timeout", 1.2))
    pp = _porcupine()             # dedicated wake-word engine, when set up

    try:
        # Imported once, here: the voice's loader thread and the speech
        # detector importing it at the same moment left the detector with
        # "requires the onnxruntime package" (3 Oct 2026, onnxruntime-gpu).
        import onnxruntime  # noqa: F401
    except Exception:
        pass
    print(f"Loading Whisper ({model_size})...")
    try:
        stt = _load_whisper(model_size, device)
    except Exception as e:
        print(f"ERROR: could not load Whisper at all: {e}")
        return

    global PROMPT_BIAS, MULTILINGUAL
    PROMPT_BIAS = build_prompt_bias()
    MULTILINGUAL = not str(model_size).endswith(".en")

    speaker = Speaker()

    try:
        listener = Listener()
    except Exception as e:
        print(f"ERROR: no microphone available ({e}).")
        return

    def confirm_aloud(tool_name: str, args: dict) -> bool:
        """Ask out loud before anything irreversible, and wait for an answer.

        Voice has no dialog box to click, and sending a message to a real
        person on a mishearing is exactly the mistake worth preventing.
        """
        summary = _describe_action(tool_name, args)

        # Two attempts: people miss the first prompt, and silently cancelling
        # what they asked for is more annoying than being asked twice.
        for attempt in (1, 2):
            question = (f"{summary}. Say yes or no."
                        if attempt == 1 else
                        f"I still need an answer. {summary}. Yes or no?")
            print(f"   [asking permission for {tool_name}, attempt {attempt}]")
            chime("ask")
            _orb_state("speaking")
            speaker.say(question)
            speaker.wait_until_done(timeout=20)
            _orb_state("listening")
            time.sleep(0.4)
            listener.drain()  # do not transcribe Athena's own question

            # Bounded wait: an unanswered question must never freeze the assistant.
            reply_audio = listener.record_utterance(1.0, max_wait=CONFIRM_TIMEOUT)
            if reply_audio is None:
                continue

            segs, _ = stt.transcribe(reply_audio, language="en", beam_size=5,
                                     without_timestamps=True)
            reply = " ".join(s.text for s in segs).strip().lower()
            print(f"you > {reply}   [answering {tool_name}]")

            if any(w in reply for w in ("yes", "yeah", "yep", "yup", "sure",
                                        "go ahead", "do it", "confirm",
                                        "okay", "ok", "send it", "please do")):
                chime("ok")
                return True
            if any(w in reply for w in ("no", "don't", "do not", "stop",
                                        "cancel", "nope", "never mind")):
                chime("no")
                speaker.say("Cancelled.")
                return False
            # Anything else is not a clear answer, so ask once more.

        chime("no")
        print(f"   [no clear answer - cancelling {tool_name}]")
        speaker.say("I didn't get a yes, so I'll skip that.")
        return False

    agent = Agent(on_step=lambda s: (print(f"   -> {s.tool}"), live.step(s.tool)),
                  confirm=confirm_aloud, voice=True)

    ok, msg = agent.llm.available()
    if not ok:
        print(msg)
        return

    # Away and back, "sorry to interrupt", welcome-backs (presence.py).
    presence.set_speaker(speaker.say, speaker.busy)
    try:
        from .. import gestures                       # back on if it was on before a restart
        gestures.start()
    except Exception as e:
        print(f"[gestures not started: {e}]")
    _start_wake_shadow()
    presence.start()
    try:
        from ..tools import brief
        brief.start_evening(presence.interject)   # the day wrapped up, once each evening
        from .. import vault
        vault.start()                             # Obsidian notes kept searchable
        from .. import backup
        backup.start()                            # data/ to OneDrive once a day, no secrets
    except Exception as e:
        print(f"[evening summary not started: {e}]")
    threading.Thread(target=_keep_warm, daemon=True, name="jarvis-warm").start()
    threading.Thread(target=_watch_wake, daemon=True, name="jarvis-wake").start()
    try:
        # Important phone messages read out. Straight into the speech queue:
        # speaker.say would also copy the message text into voice.log.
        from .. import phone_notify
        phone_notify.start(lambda line: presence.say_or_hold(line, speaker.q.put), speaker.busy)
    except Exception as e:
        print(f"[phone messages: not started ({e})]")

    # A reminder with no task of its own is just "Reminder", and prefixing
    # that gives "Reminder: Reminder" out loud.
    # Reminders ring (alarm.py) until you say stop - not one sentence you can miss.
    from .. import alarm
    scheduler.set_notifier(lambda text: alarm.ring(text, speaker.say, speaker.busy))
    alarm.start(speaker.say, speaker.busy)
    scheduler.start_scheduler("voice")

    # Phone alerts live here because voice is the process autostart always
    # launches and the only one that writes the HUD's log.
    try:
        from .. import alerts
        alerts.start()
    except Exception as e:
        print(f"[alerts unavailable: {e}]")

    # Teams classes: each morning into tasks and reminders; joined at start time.
    try:
        from ..tools import teams
        teams.start(note=lambda line: print(f"[teams] {line}") if line else None)
    except Exception as e:
        print(f"[teams: not started ({e})]")

    # Automations ("when this happens, do that"), and their "say ..." steps.
    try:
        from .. import rules
        rules.set_speaker(speaker.say)
        rules.start()
        from .. import health          # the 3 am self-check
        health.start()
        from .. import habits          # "you usually ... around now - every day?"
        habits.start(speaker.say)
        from .. import inbox           # 8 am: summary + drafted replies (never sent)
        inbox.start()
        from .. import studyauto       # 7 pm assignment plans, 8:30 pm primers
        studyauto.start()
        from .. import upes            # live attendance + timetable, every 30 min
        upes.start()
    except Exception as e:
        print(f"[automations: not started ({e})]")

    # The profile learner, for the same reason: voice is always running. It
    # only works while Athena is idle, so it never holds up a reply.
    try:
        from .. import profile
        profile.start_learner(is_idle=lambda: live._state.get("state") == "idle",
                              log=live.note)
    except Exception as e:
        print(f"[memory learner unavailable: {e}]")

    hint = f" Say '{wake}' first to address me." if wake else ""
    print(f"\nListening.{hint} Press Ctrl+C to stop.\n")
    # Jarvis-style: what's next, what's overdue - not just "Ready."
    if cfg.get("presence.greet", True):
        threading.Thread(target=presence.greet, args=("start", 0), daemon=True).start()
    else:
        speaker.say(cfg.get("assistant.greeting", "Ready."))


    # After the wake word, stay awake for a while so follow-up commands do not
    # each need "Athena" in front of them - saying it every sentence is both
    # annoying and the main source of misheard commands.
    follow_up = float(cfg.get("voice.follow_up_seconds", 8))
    log_ignored = bool(cfg.get("voice.log_ignored", False))
    awake_until = 0.0
    followups = 0
    via_wake = False
    queued: str | None = None
    _running: dict = {"t": None}
    last_said: dict = {"a": ""}
    t_stop: float | None = None   # timings: when you stopped talking
    t_heard = 0.0                 # and when the words were out of Whisper
    invited = False            # just answered a bare "Athena" with "Yes?"

    def _join_previous() -> None:
        """A stopped command cannot be killed mid-tool, only ignored; the next
        one waits for it, because two at once would share one conversation."""
        t = _running.get("t")
        if t is not None and t.is_alive():
            live.note("finishing the stopped command first")
            t.join(timeout=120)

    _STOP = re.compile(
        r"^\W*(?:ok(?:ay)?\W+|please\W+)*(?:stop(?:\s+it|\s+talking|\s+that)?|shut\s+up|"
        r"be\s+quiet|quiet|enough|that'?s\s+enough|cancel(?:\s+that|\s+it)?|never\s*mind|"
        r"hold\s+on|wait|pause|silence|leave\s+it|forget\s+it)(?:\s+please)?\W*$", re.I)

    def _echo(heard: str, said: str) -> bool:
        """The laptop's mic hears its own speaker. A chunk whose words are
        mostly what Athena is saying right now is Athena, not you."""
        h = set(re.findall(r"[a-z0-9']+", heard.lower())) - {wake.lower()}
        s = set(re.findall(r"[a-z0-9']+", said.lower()))
        return bool(h) and len(h & s) / len(h) >= 0.7

    _STOP_TAIL = re.compile(
        r"\b(?:stop(?:\s+it|\s+talking)?|enough|shut\s+up|be\s+quiet|cancel(?:\s+that)?|"
        r"never\s*mind)\W*$", re.I)
    # Only stop words. It used to end "Athena, open Spotify." - and on noise
    # Whisper repeats its primer, so a stray sound while Athena talked came
    # back as a command to open Spotify (28 Sep 2026).
    _BARGE_PROMPT = "Athena, stop. Stop. Athena, cancel. Enough."

    def _barge_in(said: str, done=lambda: False) -> str | None:
        """One short listen while Athena talks or thinks. None = nothing for
        us, "" = stop, anything else = a new command to run instead.

        Short chunks (a 4 s cap, 0.5 s of silence ends one) so "Athena, stop"
        is not glued onto half a minute of Athena's own voice, and the wake
        word is looked for anywhere in the chunk, since a chunk can start in
        the middle of Athena's sentence.
        """
        # One microphone stream for as long as Athena is busy. Reopening it
        # every 0.7 s made the speech stutter on this laptop's audio driver
        # (25 Sep 2026); `done` ends the wait the moment Athena finishes.
        audio = listener.record_utterance(0.5, cap=4.0, until=done)
        if audio is None:
            return None
        # Athena's own voice coming back through the mic, recognised by its
        # sound before any transcribing (echo.py): it used to come back as
        # "Athena." or "Stop." and cut her off.
        from .. import echo as _echo_mod
        if _echo_mod.guard.has_voice(audio) is False:
            return None
        # Primed for the words that matter here: with the general prompt and
        # a single beam, "Athena, stop" came back as "Athena, copy, plug".
        # No timestamps: nothing here reads them, and this runs on the same
        # GPU as the model while it is still writing the answer.
        segs, _ = stt.transcribe(audio, language="en", beam_size=5,
                                 initial_prompt=_BARGE_PROMPT,
                                 condition_on_previous_text=False,
                                 without_timestamps=True)
        segs = list(segs)                  # read twice: the text, then _echoes_prompt
        heard = fix_mishearings(" ".join(s.text for s in segs).strip())
        if cfg.get("voice.log_barge_in", False) or log_ignored:
            # What the mic made of itself-plus-you while Athena was busy, so a
            # missed "Athena, stop" can be traced. Only while Athena is talking
            # or thinking, i.e. right after being addressed.
            print(f"   [while busy, heard: {heard!r} ({len(audio) / SAMPLE_RATE:.1f}s)]")
        if not heard or is_hallucination(heard):
            return None
        # Primed with stop words, Whisper turns a cough or a fan into "Stop."
        # - which cancelled "what are you doing?" before Athena had said a
        # word (3 Oct 2026). A stop without her name must be clearly speech.
        # Not only a bare "Stop.": primed with "Athena, stop", Whisper also
        # made a stop out of Athena's own voice and cut her off in the middle
        # of the attendance (4 Oct 2026). No stop, named or not, on a
        # transcript the decoder itself doubted.
        unsure = (max((float(s.no_speech_prob or 0) for s in segs), default=0) > float(cfg.get("voice.primer_no_speech", 0.3))
                  or min((float(s.avg_logprob or 0) for s in segs), default=0) < float(cfg.get("voice.primer_logprob", -0.7)))
        # A bare "Stop." while Athena is talking is meant for it - the second
        # try came through as exactly that, with "Athena" lost in the noise.
        if _STOP.match(heard.strip()) and not (said and _echo(heard, said)) and not unsure:
            print(f"   [stopped - heard {heard!r} while busy]")
            return ""
        # "Arvis, stop." - the first "J" lost as the recording caught up.
        # Anything that ends in a stop word, and is not Athena's own words
        # coming back through the mic, is a stop.
        if _STOP_TAIL.search(heard) and not (said and _echo(heard, said)) and not unsure:
            print(f"   [stopped - heard {heard!r} while busy]")
            return ""
        if wake:
            m = re.search(rf"\b(?:{re.escape(wake)}|athina|atheena|athene|atena|thena|athenas)\b"
                          rf"[\s,.!?]*(.*)$", heard, re.I)
            if not m:
                return None
            rest = m.group(1).strip(" .!?,")
            if not rest and said and wake.lower() in said.lower():
                return None                  # Athena saying its own name
        else:
            rest = heard.strip(" .!?,")
        if rest and said and _echo(rest, said):
            return None
        if rest and _echoes_prompt(heard, segs, len(audio) / SAMPLE_RATE, _BARGE_PROMPT + " " + PROMPT_BIAS):
            print(f"   [ignored while busy: {heard!r} - the speech primer, not you]")
            return None
        if not rest or _STOP.match(rest):
            if unsure:                       # "Athena." out of her own voice or noise
                return None
            if not rest:
                return _JUST_NAME            # her name alone: you want her attention, not silence
            print(f"   [stopped - heard {heard!r} while busy]")
            return ""
        return rest

    while True:
        hindi_turn = False
        try:
            if _ENGAGED and time.time() >= awake_until:
                _engage(False)
                invited = False

            # While engaged, stop waiting when the follow-up window lapses,
            # so the orb comes down on time instead of hanging on screen until
            # somebody happens to make a noise.
            wait_cap = max(1.0, awake_until - time.time()) if _ENGAGED else None
            if queued is not None:
                # Said over Athena while it was talking or thinking.
                text, queued = queued, None
                via_wake = True
                print(f"you > {text}")
                live.heard(text)
                presence.talked()
                _engage(True)
                _orb_state("thinking")
            else:
                pre = None
                if pp is not None and not _ENGAGED:
                    # The wake-word engine listens for the name; Whisper waits.
                    pre = listener.wait_for_wake(pp)
                    earcon()                     # heard the name: say so at once
                    _engage(True)
                    _orb_state("listening")
                    # The command usually follows the name without a pause.
                    audio = listener.record_utterance(timeout, max_wait=1.8 if pre == "word" else 6.0)
                    if audio is None:
                        if pre == "word":        # just "Athena" - someone getting our attention
                            awake_until = time.time() + follow_up
                            followups = int(cfg.get("voice.follow_ups", 3))
                            _orb_state("speaking")
                            speaker.say("Yes?")
                            invited = True
                            speaker.wait_until_done(timeout=20)
                            listener.drain()
                            _orb_state("listening")
                        else:
                            _engage(False)
                        continue
                else:
                    audio = listener.record_utterance(timeout, max_wait=wait_cap)
                    if audio is None:
                        continue
                t_stop = time.time() - listener.last_wait     # you stopped talking about here
                # Just her own last sentence trailing out of the speaker.
                from .. import echo as _echo_mod
                if _echo_mod.guard.has_voice(audio) is False:
                    if log_ignored:
                        print("   [ignored: only Athena's own voice]")
                    continue

                # Priming the decoder with the words we expect makes a small model
                # far better at proper nouns - "Athena" instead of "Here's your wrist".
                # A multilingual model hears English or Hindi (hindi.py); any
                # other language it thinks it heard is noise or an accent, and
                # is decoded again as English.
                audio = _level(audio)
                multi = MULTILINGUAL and cfg.get("language.hindi", True)
                segments, info = stt.transcribe(
                    audio, language=None if multi else "en", beam_size=5,
                    initial_prompt=PROMPT_BIAS,
                    condition_on_previous_text=False,
                    word_timestamps=True,
                )
                segments = list(segments)
                lang = getattr(info, "language", "en") if multi else "en"
                if lang not in ("en", "hi") or (lang == "hi" and getattr(info, "language_probability", 1) < 0.6):
                    segments, _ = stt.transcribe(audio, language="en", beam_size=5, initial_prompt=PROMPT_BIAS,
                                                 condition_on_previous_text=False, word_timestamps=True)
                    segments, lang = list(segments), "en"
                hindi_turn = lang == "hi"
                t_heard = time.time()
                # No orb yet - we do not know if this was meant for us. It lights
                # up below, once the wake word has actually been found.
                from ..agent import clean_speech
                text = clean_speech(fix_mishearings(_from_last_name(_trusted_text(segments))))
                if hindi_turn:
                    from .. import hindi
                    text = hindi.wake_to_latin(text)
                # "yes", "two", "all" are too short to count as commands - except
                # as the answer to a question Athena just asked.
                answering = invited and bool(offer.REPLY.match(text))
                if is_hallucination(text) and not answering:
                    _engage(False)
                    continue
                if _echoes_prompt(text, segments, len(audio) / SAMPLE_RATE, PROMPT_BIAS):
                    print(f"   [ignored: {text!r} - Whisper repeating its primer, not you]")
                    _engage(False)
                    continue

                spoken = text.lower()
                # Print only once we know it was meant for us, unless logging
                # everything is explicitly switched on. Anything said near this
                # machine should not land in a file by default.
                # One decision, used for both printing and acting. These were two
                # separate checks and could disagree - the line got printed as
                # addressed, then dropped as not addressed, so nothing happened and
                # nothing explained why.
                heard_wake, remainder = (_strip_wake(text, wake, wake_at_start)
                                         if wake else (True, text))
                # The prompt primes Whisper to expect "Athena", which is what
                # makes it hear the name - and on 25 Sep 2026 it also heard it
                # in a video playing nearby ("Athena, hang on there"). A real
                # "Athena" comes back with high confidence on the word itself;
                # a sound bent into the name does not.
                if pre == "word":
                    # The engine heard the name; Whisper has the rest, with or
                    # without the name at the front.
                    if not heard_wake:
                        heard_wake, remainder = True, text
                elif heard_wake and wake:
                    p = _wake_probability(segments)
                    # Your own "Athena" scored 0.18-0.41 across the room (25 Sep
                    # 2026), so a middling score is let through when what
                    # follows is plainly a command; video chatter is not.
                    # A bare "Athena" came back at 0.35-0.51 with small.en and was
                    # ignored four times in a row (26 Sep 2026); noise sits at
                    # 0.00-0.03. So the bare name passes from 0.3.
                    # The word's own score stopped meaning anything once the mic's
                    # effects were off: real commands ("Athena, do I have any
                    # pending tasks?", "Athena.") came back at 0.00 and were
                    # dropped all day (5 Oct 2026). Whisper's confidence in the
                    # WHOLE sentence still separates you from noise and TV,
                    # which come back as low-confidence gibberish.
                    seg_lp = min((float(s.avg_logprob or 0) for s in segments), default=-9.0)
                    seg_ns = max((float(s.no_speech_prob or 0) for s in segments), default=1.0)
                    # -0.8 dropped real ones ("Athena." at -1.04, "Athena, enough." at -1.00,
                    # 6 Oct 2026); a confident "this is speech" (low no-speech) earns a wider margin.
                    clear = ((seg_lp >= float(cfg.get("voice.wake_sentence_logprob", -0.8))
                              or (seg_lp >= -1.1 and seg_ns <= 0.55))
                             and seg_ns <= float(cfg.get("voice.wake_sentence_no_speech", 0.55))
                             and (not remainder or _COMMANDISH.match(remainder)
                                  or len(remainder.split()) <= 12))
                    if p is not None and p < float(cfg.get("voice.wake_min_prob", 0.6)) and not clear and not (
                            (p >= float(cfg.get("voice.wake_floor_prob", 0.1))
                             and _COMMANDISH.match(remainder or ""))
                            or (p >= float(cfg.get("voice.wake_bare_prob", 0.3))
                                and not _FILLER_ONLY.match(remainder or ""))):
                        # What was heard, now that "Athena doesn't listen sometimes" needs
                        # evidence (4 Oct 2026) - only sentences starting with her name,
                        # i.e. meant for her; other room speech is still never written.
                        print(f"   [wake word too uncertain ({p:.2f}, sentence {seg_lp:.2f}/{seg_ns:.2f})"
                              f" - ignored: {text[:80]!r}]")
                        heard_wake = False
                still_awake = _ENGAGED and time.time() < awake_until and followups > 0
                # The HUD's mic button stands in for saying "Athena".
                tapped = live.take_wake()
                if tapped:
                    still_awake = True
                # A follow-up that is mostly Athena's own last answer is the
                # speaker's tail coming back through the mic ("Command Center,
                # which is currently activated", 25 Sep 2026), not you.
                # "Athena, yeah" / "Athena, okay" - the name, then nothing to do.
                if heard_wake and remainder and _FILLER_ONLY.match(remainder):
                    heard_wake = False
                # Without the name, only something that sounds like a command
                # counts as a follow-up. Song lyrics came through as "We'll see
                # you in a minute" and "And then the ocean..." (26 Sep 2026).
                if still_awake and not heard_wake and not tapped and not invited \
                        and not _followish(text):
                    still_awake = False
                # Not for an answer: "all" is mostly Athena's own "...a number,
                # or all." and would be thrown away as her echo.
                if still_awake and not heard_wake and not tapped and last_said["a"] \
                        and not answering and _echo(text, last_said["a"]):
                    still_awake = False
                addressed = (not wake) or heard_wake or still_awake
                if addressed:
                    invited = False
                # A command that named Athena (or the mic button) gives a fresh
                # budget of voice.follow_ups; each follow-up spends one, so a
                # conversation runs a few turns without the name but a video
                # playing nearby can't keep her answering it line after line.
                via_wake = heard_wake or tapped or not wake
                if addressed and not via_wake:
                    followups -= 1
                if addressed or log_ignored:
                    print(f"you > {text}")
                if addressed:
                    live.heard(text)
                    presence.talked()
                if any(w in spoken for w in ("stop listening", "goodbye athena", "shut down athena")):
                    speaker.say("Going quiet.")
                    live.state("stopped")     # asked to stop: not to be restarted
                    time.sleep(2)
                    return

                if wake:
                    if not addressed:
                        if log_ignored:
                            print("   [no wake word, ignoring]")
                        _engage(False)
                        continue
                    # Addressed by name, or still inside the follow-up window.
                    if pre != "word":            # the wake engine already chimed
                        earcon()
                    _engage(True)
                    _orb_state("thinking")
                    if heard_wake:
                        text = remainder or text
                        # "Athena" on its own is someone getting our attention
                        if not remainder:
                            awake_until = time.time() + follow_up
                            followups = int(cfg.get("voice.follow_ups", 3))
                            _orb_state("speaking")
                            speaker.say("Yes?")
                            invited = True
                            speaker.wait_until_done(timeout=20)
                            listener.drain()
                            _orb_state("listening")
                            continue

            # Hindi: translated now - only once it is known to be meant for
            # Athena - and answered in Hindi. "...in Hindi" asks for a Hindi
            # answer to an English question.
            reply_hindi = hindi_turn or bool(re.search(r"\b(?:in|into)\s+hindi\W*$", text, re.I))
            if cfg.get("language.reply", "match") == "english":
                reply_hindi = False
            if hindi_turn:
                from .. import hindi
                try:
                    english = hindi.to_english(text)
                    # English in an Indian accent can be taken for Hindi; if
                    # "translating" it changes nothing, it was English.
                    same = re.sub(r"\W+", " ", english).strip().lower() == re.sub(r"\W+", " ", text).strip().lower()
                    if same:
                        reply_hindi = bool(re.search(r"\b(?:in|into)\s+hindi\W*$", text, re.I))
                    else:
                        print(f"   (in English: {english})")
                    text = english
                except Exception as e:
                    print(f"   [hindi: could not translate ({str(e)[:80]})]")
            hindi_turn = False

            # "Athena, stop" on its own: stop whatever is running, say nothing.
            # Sent to the model it became a 2-minute runaway that the next
            # command then queued behind (27 Sep 2026).
            if _STOP.match(text.strip()):
                agent.cancelled = True
                speaker.stop()
                # ...including a ringing alarm: "Athena, stop" was caught here and the
                # alarm rang on through three of them (3 Oct 2026).
                from .. import alarm
                if alarm.ringing():
                    alarm.stop()
                    print("athena > (alarm off)")
                    live.note("alarm off")
                print("athena > (stopped)")
                live.note("stopped")
                listener.drain()
                _orb_state("idle")
                continue

            # Think in the background, listening meanwhile, so "Athena, stop"
            # or a new command is heard while the old one is still running.
            _join_previous()
            t_ask = time.time()
            n_started = len(speaker.started)

            def _say_part(sentence: str, _hi=reply_hindi) -> None:
                _orb_state("speaking")
                speaker.say(sentence, hindi=_hi)
            agent.speak = _say_part
            box: dict = {}
            worker = threading.Thread(target=lambda: box.update(a=agent.ask(text)),
                                      daemon=True, name="jarvis-ask")
            _running["t"] = worker
            worker.start()
            cut = None
            while worker.is_alive():
                # Athena may already be talking (streamed sentences) - pass what
                # she is saying so her own words are not taken for a command.
                # Everything said this turn, not only the sentence playing now:
                # between sentences that was "", and her own "What can I do for
                # you today" came back as your command (4 Oct 2026).
                got = _barge_in(" ".join(x for x in (agent.spoken, speaker.current if speaker.busy() else "") if x),
                                done=lambda: not worker.is_alive())
                if got is not None:
                    cut = got
                    break
            if cut is None:
                answer = box.get("a", "")
                t_done = time.time()
                last_said["a"] = answer
                live.replied(answer)
                _orb_state("speaking")
                done_aloud = agent.spoken.strip()
                if not done_aloud:
                    speaker.say(answer, hindi=reply_hindi)
                elif answer.strip() != done_aloud:
                    # The streamed part was cut short (a guard stopped it) or
                    # the answer changed: say what has not been said.
                    rest = answer.strip()[len(done_aloud):] if answer.strip().startswith(done_aloud) else answer
                    if rest.strip():
                        speaker.say(rest.strip(), hindi=reply_hindi)
                if cfg.get("voice.log_timings", True):
                    first = next((t for t in speaker.started[n_started:]), None)
                    if first is None:
                        for _ in range(40):            # up to 2 s for SAPI to start
                            time.sleep(0.05)
                            if len(speaker.started) > n_started:
                                first = speaker.started[n_started]
                                break
                    parts = []
                    if t_stop:
                        parts += [f"end-of-speech wait {listener.last_wait:.2f}s", f"hear {t_heard - t_stop - listener.last_wait:.2f}s"]
                    parts.append(f"think {t_done - t_ask:.2f}s")
                    if first:
                        parts.append(f"first word {first - (t_stop or t_ask):.2f}s after "
                                     + ("you stopped" if t_stop else "the command"))
                    print("   [time: " + " · ".join(parts) + (" · streamed" if done_aloud else "") + "]")
                    t_stop = None
                while speaker.busy():
                    got = _barge_in(f"{answer} {speaker.current}", done=lambda: not speaker.busy())
                    if got is not None:
                        cut = got
                        break
            # No follow-up window once music is on, or while a video plays
            # (presence._watching): the mic is then mostly hearing that. The
            # Athena reel playing in the browser said "Athena..." and then "find
            # his phone", and she spent three minutes looking for it (9 Oct 2026).
            if not last_said["a"].startswith("Playing") and not presence._watching():
                if via_wake:
                    followups = int(cfg.get("voice.follow_ups", 3))
                if via_wake or followups > 0:            # the conversation carries on
                    awake_until = time.time() + follow_up
            # Athena asked something ("Which one? Say a number, or all.",
            # "Want me to open it?"): listen for the answer without the wake
            # word, and let a one-word reply through.
            if cut is None and agent.expects_reply:
                awake_until = time.time() + float(cfg.get("voice.reply_seconds", 10))
                followups = max(followups, 1)
                invited = True

            if cut is not None:
                agent.cancelled = True        # the old turn stops generating and talking
                speaker.stop()
                if cut == _JUST_NAME:
                    # "Athena" on its own while she was busy: you want her
                    # attention. It used to stop her and drop what you said
                    # next, so every question had to be asked twice (4 Oct 2026).
                    live.note("interrupted - listening")
                    time.sleep(0.2)
                    listener.drain()
                    _orb_state("speaking")
                    speaker.say("Yes?")
                    speaker.wait_until_done(timeout=5)
                    listener.drain()
                    invited = True
                    awake_until = time.time() + follow_up
                    followups = max(followups, 1)
                    _engage(True)
                    _orb_state("listening")
                    continue
                if cut:
                    live.note(f"interrupted - new command: {cut[:60]}")
                    queued = cut
                else:
                    live.note("interrupted - stopped")
                    from .. import alarm
                    if alarm.ringing():
                        alarm.stop()
                    print("athena > (stopped)")
                time.sleep(0.2)
                listener.drain()
                _orb_state("idle" if not cut else "thinking")
                continue

            # Wait for the reply to finish playing, then bin whatever the mic
            # picked up - otherwise Athena transcribes its own answer and can
            # trigger itself.
            time.sleep(0.4)
            listener.drain()
            # Waiting on an answer: stay visibly listening. Otherwise work
            # done - off screen again.
            _orb_state("listening" if invited else "idle")

        except KeyboardInterrupt:
            print("\nStopped.")
            return
        except Exception as e:
            print(f"[error: {e}]")
            # Never leave the orb stuck on screen because a step blew up.
            _orb_state("idle")
            continue


def run() -> None:
    """Start the orb (main thread) and the voice loop (worker thread).

    Tk insists on owning the main thread, and the voice loop blocks on the
    microphone, so they cannot share one. The orb is optional: if it fails to
    start for any reason, voice still runs exactly as before.
    """
    global _ORB

    if cfg.get("overlay.enabled", True):
        try:
            from .overlay import Orb

            _ORB = Orb(
                size=int(cfg.get("overlay.size", 200)),
                position=cfg.get("overlay.position", "center"),
                hide_when_idle=bool(cfg.get("overlay.hide_when_idle", False)),
            )
        except Exception as e:
            print(f"[overlay unavailable: {e} - continuing without it]")
            _ORB = None

    if _ORB is None:
        _voice_loop()
        return

    def worker() -> None:
        try:
            _voice_loop()
        finally:
            _ORB.stop()

    threading.Thread(target=worker, daemon=True, name="jarvis-voice").start()
    try:
        _ORB.run()
    except KeyboardInterrupt:
        _ORB.stop()


if __name__ == "__main__":
    sys.exit(run())
