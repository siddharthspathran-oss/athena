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
        # The short lines are made once at load and played without a synthesis.
        deadline = time.time() + 3
        while len(sp._clips) < len(voice.CLIPS) and time.time() < deadline:
            time.sleep(0.02)
        made = []
        sp._neural.synth = lambda text, **k: made.append(text) or np.zeros(10, dtype=np.float32)
        played.clear()
        sp.say("On it.")
        if not sp.wait_until_done(3) or len(played) != 1 or made:
            bad.append(f"a saved clip was made again ({made}) or not played ({len(played)})")
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
    for t in ("and then the ocean", "we'll see you in a minute", "yeah", "the match was crazy man",
              # all heard and answered on 13-14 Oct 2026, before follow-ups were made strict
              "Please like, share, and subscribe.", "This is Jack Kuchiyama.", "deal with her", "it's",
              "you need to let", "Good morning everyone and welcome back to my channel", "can you believe it"):
        if _followish(t):
            bad.append(f"room talk taken as a follow-up: {t!r}")
    from jarvis.ui.voice import _reply_ok
    for t in ("yes", "the second one", "friday at 5"):
        if not _reply_ok(t):
            bad.append(f"short answer to a question refused: {t!r}")
    if _reply_ok("This is Jack Kuchiyama and I am here with you today"):
        bad.append("a long room sentence taken as an answer")


def check_continuation(bad):
    """'Athena, [pause] set the volume to fifty' said over her: the part after
    the name is the command, not a reason to answer 'Yes?' (16 Oct 2026)."""
    from types import SimpleNamespace
    import numpy as np
    from jarvis.ui.voice import _continuation

    class FakeSTT:
        def __init__(self, text, lp=-0.2, ns=0.05):
            self.seg = SimpleNamespace(text=text, avg_logprob=lp, no_speech_prob=ns)

        def transcribe(self, *_a, **_k):
            return [self.seg], None

    audio = np.zeros(16000 * 3, dtype=np.float32)        # ~3 s: a phrase and the pause after it
    for heard, want in (("set the volume to fifty.", "set the volume to fifty"),
                        ("Athena, is Alexa connected?", "is Alexa connected"),
                        ("'s Bluetooth on", "Bluetooth on")):
        got = _continuation(FakeSTT(heard), audio, "athena")
        if got.lower() != want.lower():
            bad.append(f"continuation {heard!r} -> {got!r}, want {want!r}")
    if _continuation(FakeSTT("", ns=0.95), audio, "athena"):
        bad.append("silence after the name taken as a command")
    if _continuation(FakeSTT("set the volume", lp=-1.6, ns=0.8), audio, "athena"):
        bad.append("mumbled noise after the name taken as a command")


def check_hold_to_talk(bad):
    """The key held for 1 s of near-silence: exactly that span comes back,
    marked as meant for her - no loudness gate, no silence wait. A key
    pressed with it (a shortcut) throws the recording away."""
    import queue as _queue
    from jarvis.ui import voice_mic                 # the Listener looks them up there
    voice_mic._orb_state = voice_mic._orb_level = lambda *_: None
    block = np.full((voice.BLOCK, 1), 0.001, dtype=np.float32)     # under any loudness gate

    def listen(held_from, held_to, abort_at=None):
        lis = voice.Listener()
        lis._ensure_stream = lambda: None
        n = [0]

        class Blocks:
            def get(self, timeout=None):
                n[0] += 1
                if n[0] > held_to + 3:
                    raise _queue.Empty
                lis.held = held_from <= n[0] < held_to
                if n[0] == abort_at:
                    lis.aborted = True
                return block

            def get_nowait(self):
                raise _queue.Empty

        lis.blocks = Blocks()
        voice._vad()                           # loaded at start-up in real use; here it would eat max_wait
        return lis, lis.record_utterance(0.65, max_wait=2.0)

    lis, audio = listen(5, 15)                 # held for blocks 5-14 (1.0 s), 0.3 s before it kept
    if audio is None or not lis.pushed:
        bad.append(f"hold-to-talk: nothing returned for a 1 s hold (pushed={lis.pushed})")
    elif not 1.0 <= len(audio) / voice.SAMPLE_RATE <= 1.5:
        bad.append(f"hold-to-talk: {len(audio) / voice.SAMPLE_RATE:.1f} s returned for a 1 s hold")
    lis, audio = listen(5, 15, abort_at=8)
    if audio is not None:
        bad.append("hold-to-talk: a shortcut (another key with it) was taken as a sentence")


def check_not_twice(bad):
    """A streamed answer isn't said again at the end (25 Oct 2026: a list of five files, twice)."""
    spoken = ('I found several files with "find me" in your documents: 1. **Workbook.docx** - about industry connect. '
              '2. **Start here.md** - your vault. Would you like me to open any of these?')
    answer = ('I found several files with "find me" in your documents:\n\n1. **Workbook.docx** - about industry connect\n'
              '2. **Start here.md** - your vault\n\nWould you like me to open any of these?')
    if voice._unsaid(answer, spoken):
        bad.append(f"a fully streamed answer would be said again: {voice._unsaid(answer, spoken)!r}")
    if voice._unsaid(answer, "I found several files with \"find me\" in your documents:") != \
            "1. **Workbook.docx** - about industry connect\n2. **Start here.md** - your vault\n\nWould you like me to open any of these?":
        bad.append(f"the unsaid rest is wrong: {voice._unsaid(answer, 'I found several files with \"find me\" in your documents:')!r}")


def check_speakers_gate(bad):
    """Sound from the speakers (a Teams class) isn't taken for a command; short controls still are."""
    pb = voice._Playback.__new__(voice._Playback)
    from collections import deque
    pb.loud, pb.headphones = deque([100.0, 100.1, 100.2]), False
    if not pb.during(99.0, 100.05) or pb.during(101.0, 102.0):
        bad.append("playback window wrong")
    pb.headphones = True
    if pb.during(99.0, 100.05):
        bad.append("sound in headphones was treated as heard by the mic")
    for said in ("pause", "next song", "stop", "volume down", "turn it down", "skip this one"):
        if not voice._MEDIA_CMD.match(said):
            bad.append(f"{said!r} should still work while music plays")
    for said in ("we're super lucky", "send you a message saying hello", "find me", "do you have a question for me"):
        if voice._MEDIA_CMD.match(said):
            bad.append(f"{said!r} would get through while a class plays")


if __name__ == "__main__":
    bad: list[str] = []
    checks = (check_player, check_fallback, check_text, check_failure_log, check_follow_ups, check_continuation,
              check_hold_to_talk, check_not_twice, check_speakers_gate)
    for check in checks:
        check(bad)
    for b in bad:
        print("FAIL", b)
    print(f"{len(checks)} groups of checks, {len(bad)} failure(s)")
    sys.exit(1 if bad else 0)
