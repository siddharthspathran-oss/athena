"""Checks the voice pipeline pieces that do not need a microphone: the
natural-voice player (queued sentences in order, "stop" silences it and
drops the rest), the fallback to the Windows voice, how text is prepared for
speaking, the varied fixed lines, and the failure log.

    python tests/voicetest.py
"""
import ctypes
import json
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not hasattr(ctypes, "WinDLL"):
    from unittest import mock

    ctypes.WinDLL = lambda *a, **k: mock.MagicMock()
    ctypes.windll = mock.MagicMock()
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    for name in ("win32gui", "win32con", "win32api", "win32process", "win32clipboard",
                 "pycaw", "pycaw.pycaw", "comtypes", "comtypes.client", "winsound",
                 "keyboard", "send2trash", "playwright", "playwright.sync_api", "ddgs",
                 "pythoncom", "winreg", "msvcrt"):
        try:
            __import__(name)
        except Exception:
            sys.modules[name] = mock.MagicMock()

# A stand-in speaker: records what would have played.
played, stopped = [], []
fake_sd = types.ModuleType("sounddevice")
fake_sd.play = lambda audio, rate: played.append(len(audio))
fake_sd.stop = lambda: stopped.append(1)
fake_sd.InputStream = object
sys.modules["sounddevice"] = fake_sd

from jarvis import agent, failures, tts  # noqa: E402
from jarvis.config import cfg  # noqa: E402
from jarvis.ui import voice  # noqa: E402


class FakeVoice(tts.Engine):
    name, sample_rate, device = "fake", 1000, "cpu"

    def synth(self, text):
        time.sleep(0.03)
        return np.zeros(len(text) * 10, dtype=np.float32)       # 10 ms a character


def check_player(bad):
    real_load = tts.load
    tts.load = lambda log=print: FakeVoice()
    voice.Speaker._make_voice = lambda self: None
    try:
        sp = voice.Speaker()
        deadline = time.time() + 3
        while sp._neural is None and time.time() < deadline:
            time.sleep(0.01)
        if sp._neural is None:
            bad.append("the neural voice never took over from the Windows voice")
            return
        for s in ("First sentence.", "Second one.", "Third."):
            sp.say(s)
        if not sp.wait_until_done(5) or len(played) != 3:
            bad.append(f"three sentences, {len(played)} played")
        played.clear()
        for s in ("A long first sentence that keeps going on.", "Two.", "Three.", "Four."):
            sp.say(s)
        time.sleep(0.12)
        sp.stop()
        time.sleep(0.6)
        if len(played) > 1 or sp.busy():
            bad.append(f"after stop: {len(played)} played, busy={sp.busy()}")
        sp.say("Next answer.")
        if not sp.wait_until_done(3) or not played:
            bad.append("nothing spoken after a stop")
    finally:
        tts.load = real_load


def check_fallback(bad):
    real = tts.VOICES
    tts.VOICES = Path(tempfile.mkdtemp())          # no voices downloaded
    cfg._d.setdefault("voice", {})
    saved = cfg._d["voice"].get("engine")
    try:
        for engine in ("auto", "kokoro", "piper", "sapi"):
            cfg._d["voice"]["engine"] = engine
            if tts.load(log=lambda *_: None) is not None:
                bad.append(f"engine {engine} with no voice files did not fall back to Windows")
    finally:
        tts.VOICES = real
        cfg._d["voice"]["engine"] = saved


def check_text(bad):
    for raw, want in (("Opened SS_Unit_2_notes.pdf, from Documents.", "Opened SS Unit 2 notes PDF, from Documents."),
                      ("Battery 81%, charging.", "Battery 81 percent, charging."),
                      ("Opened report.docx in Word.", "Opened report in Word."),
                      ("R&D and/or Q&A", "R and D and or Q and A")):
        got = voice._for_speech(raw)
        if got != want:
            bad.append(f"_for_speech({raw!r}) = {got!r}, want {want!r}")
    lines = {agent._pick_line("hello") for _ in range(20)}
    if len(lines) < 2:
        bad.append("the greeting never varies")
    a, b = agent._pick_line("missed"), agent._pick_line("missed")
    if a == b:
        bad.append("the same fixed line twice in a row")


def check_failure_log(bad):
    from types import SimpleNamespace as NS

    from jarvis import memory, profile, recall

    tmp = Path(tempfile.mkdtemp())
    failures.FILE = tmp / "failures.jsonl"
    memory.MEM_FILE, profile.PROFILE = tmp / "memory.json", tmp / "profile.md"
    profile.journal = recall.note_voice = lambda *a, **k: None
    cfg._d.setdefault("planner", {})["rescue"] = False

    class Claims:
        def chat(self, *a, **k):
            return NS(content="Done, I sent it.", tool_calls=None)

    a = agent.Agent()
    a.llm = Claims()
    agent.planner.is_multistep = lambda t: False
    a.ask("um")                                          # not understood
    a.ask("email rahul the dsp notes")                   # claimed, nothing ran
    rows = [json.loads(x) for x in failures.FILE.read_text(encoding="utf-8").splitlines()]
    kinds = [r["kind"] for r in rows]
    if "not understood" not in kinds or not any("without a tool" in k for k in kinds):
        bad.append(f"failure log: {kinds}")
    if "failure(s)" not in failures.report():
        bad.append("run.py failures report is empty")


def check_follow_ups(bad):
    """Said within the follow-up window, without "Athena": what is meant for her
    counts, room talk and lyrics do not (9 Oct 2026)."""
    from jarvis.ui.voice import _followish
    for t in ("and tomorrow?", "what about DSA", "also open it", "okay now play something",
              "why is that?", "same for probability", "then remind me at 5"):
        if not _followish(t):
            bad.append(f"follow-up not taken: {t!r}")
    for t in ("and then the ocean", "we'll see you in a minute", "yeah", "the match was crazy man"):
        if _followish(t):
            bad.append(f"room talk taken as a follow-up: {t!r}")


if __name__ == "__main__":
    bad: list[str] = []
    for check in (check_player, check_fallback, check_text, check_failure_log, check_follow_ups):
        check(bad)
    for b in bad:
        print("FAIL", b)
    print(f"5 groups of checks, {len(bad)} failure(s)")
    sys.exit(1 if bad else 0)
