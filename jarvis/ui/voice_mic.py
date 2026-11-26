"""Hearing: the microphone, hold-to-talk, the speech detector and the speakers' meter.

Listener records one utterance at a time (or the span the hold key was down);
_Playback knows when the speakers were playing, so their sound isn't taken for
you. Split out of voice.py (31 Oct 2026); voice.py imports all of it back.
"""
from __future__ import annotations

import queue
import re
import sys
import threading
import time

import numpy as np

from .. import live
from ..config import cfg
from .voice_hud import _orb_state, _orb_level


SAMPLE_RATE = 16000
BLOCK = 1600          # 100 ms blocks
MIN_SPEECH_S = 0.4    # ignore blips shorter than this
MAX_UTTERANCE_S = 15.0  # hard cap on one recording
HOLD_MAX_S = 60.0     # hard cap on one hold-to-talk recording (a key stuck down)
MENU_MASK = 0xE8      # an unassigned virtual key: sent while an Alt hold key is down (_watch_hold_key)
PARTIAL_BLOCKS = 4    # while the key is held, the words so far are read again every 0.4 s (the Stage)


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
        self.held = False         # the hold-to-talk key is down (_watch_hold_key)
        self.aborted = False      # another key went down with it: a shortcut, not a sentence
        self.pushed = False       # the last recording was made with the key held: it was meant for her
        self.on_partial = None    # (audio so far) -> None: the Stage's live words while the key is held

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
            if self.held:
                return "key"
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
        pushing = False
        self.pushed = False

        self._ensure_stream()
        while True:
            if max_wait is not None and not speaking and not self.held:
                if time.time() - started_waiting > max_wait:
                    return None, False
            if until is not None and not speaking and not self.held and until():
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
            # Hold-to-talk: the key decides where the sentence starts and
            # ends - no loudness gate, no wait for silence, no wake word.
            if self.held and not pushing:
                pushing = True
                # What was already being recorded, or the 0.3 s before the press.
                captured = captured if speaking else list(preroll)[-3:]
                preroll.clear()
                _orb_state("listening")
            if pushing:
                captured.append(block)
                if self.held and len(captured) * BLOCK / SAMPLE_RATE < HOLD_MAX_S:
                    if self.on_partial and len(captured) % PARTIAL_BLOCKS == 0:
                        self.on_partial(np.concatenate(captured).flatten())
                    continue
                audio = np.concatenate(captured).flatten()
                if self.aborted or len(audio) / SAMPLE_RATE < MIN_SPEECH_S:      # a shortcut or a tap
                    pushing = speaking = False
                    captured = []
                    continue
                self.last_wait, self.pushed = 0.0, True
                return audio, False
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


def _watch_hold_key(listener: Listener, speaker: "Speaker") -> str:
    """Hold-to-talk (voice.hold_key): hold it, speak, let go - she answers.

    Hedwig's Space bar (23 Oct 2026). The key marks where the sentence starts
    and ends, so there is no wake word, no wait for silence, and no room
    chatter caught on the way: since 16 Oct nearly every fumble left was a
    mis-heard fragment ("photoshoot, stay away from me, sir" sent a message).
    Pressing it while she talks cuts her off. Another key pressed with it is
    a shortcut (Ctrl+C), and that recording is thrown away.
    Returns the key's name, or "" when off or unavailable.
    """
    key = str(cfg.get("voice.hold_key", "right alt") or "").strip().lower()
    if not key:
        return ""
    try:
        import keyboard
    except Exception as e:
        print(f"[hold-to-talk off: {e}]")
        return ""
    # By name: Right Ctrl shares a scan code with the left one. Right Alt is
    # named "alt gr" on some layouts and "right menu" by some Windows builds.
    names = {"right alt": {"right alt", "alt gr", "right menu"}}.get(key, {key})
    is_alt = "alt" in key or "menu" in key

    def mask_menu() -> None:
        # Alt pressed and released on its own opens the window's menu bar.
        # An unassigned key (vk E8) while it is down makes it a combination,
        # so letting go does nothing - AutoHotkey's "menu mask key".
        import ctypes
        ctypes.windll.user32.keybd_event(MENU_MASK, 0, 0, 0)
        ctypes.windll.user32.keybd_event(MENU_MASK, 0, 2, 0)          # KEYEVENTF_KEYUP

    def on_event(e) -> None:
        if e.scan_code == -MENU_MASK:              # our own mask key coming back through the hook
            return
        if (e.name or "").lower() in names:
            if e.event_type == "down" and not listener.held:
                listener.aborted, listener.held = False, True
                if is_alt:
                    mask_menu()
                if speaker.busy():
                    speaker.stop()
            elif e.event_type == "up":
                listener.held = False
        elif e.event_type == "down" and listener.held:
            listener.aborted = True

    keyboard.hook(on_event)
    return key


class _Playback:
    """When something else is coming out of the speakers - a class on Teams, a
    video, music - read from Windows' output meter ten times a second.

    Whisper is primed for her name, so a teacher's sentence came out as
    "Athena, ..." clearly enough to pass every check: 18 of the 58 "commands"
    from 21 to 28 Oct 2026 were a Teams lecture ("Athena, we're super lucky",
    "send you a message saying hello"). Her own voice doesn't count
    (speaker.busy), nor does sound in headphones, which the mic can't hear.
    """

    PEAK = 0.03                       # silence reads 0.00-0.01; speech and music 0.1-0.7
    # The meter is read in a small process of its own, not here: read in this process, the
    # COM objects it made were freed on other threads and the voice process crashed every
    # two or three minutes (access violation, 28 Oct 2026). A new one every ten minutes
    # follows a change of output (headphones on or off).
    METER = (
        "import time\n"
        "from ctypes import POINTER, cast\n"
        "from comtypes import CLSCTX_ALL\n"
        "from pycaw.pycaw import AudioUtilities, IAudioMeterInformation\n"
        "dev = AudioUtilities.GetSpeakers()\n"
        "meter = cast(dev._dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None), POINTER(IAudioMeterInformation))\n"
        "print('device', dev.FriendlyName, flush=True)\n"
        "end = time.time() + 600\n"
        "while time.time() < end:\n"
        "    print(f'{meter.GetPeakValue():.3f}', flush=True)\n"
        "    time.sleep(0.1)\n")

    def __init__(self, speaker) -> None:
        from collections import deque
        self.speaker, self.loud, self.headphones = speaker, deque(maxlen=900), False
        threading.Thread(target=self._loop, daemon=True, name="playback-meter").start()

    def _loop(self) -> None:
        import subprocess
        quiet_until = 0.0
        while True:
            try:
                p = subprocess.Popen([sys.executable, "-c", self.METER], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for line in p.stdout:
                    if line.startswith("device "):
                        self.headphones = bool(_HEADPHONES.search(line))
                    elif self.speaker.busy():
                        quiet_until = time.time() + 0.4                 # her own voice, and its tail
                    elif time.time() > quiet_until and float(line) > self.PEAK:
                        self.loud.append(time.time())
                p.wait()
            except Exception:
                pass
            time.sleep(2)

    def during(self, start: float, end: float) -> bool:
        """Was something else playing aloud between these two moments?"""
        return not self.headphones and any(start <= t <= end + 0.3 for t in list(self.loud))

    def recent(self, seconds: float) -> bool:
        return self.during(time.time() - seconds, time.time())


_HEADPHONES = re.compile(r"head|hands-?free|buds|airpods|wh-|wf-|earphone|jabra|bose|sony", re.I)


class _OneAtATime:
    """Whisper, one transcription at a time. The Stage's live words run on their
    own thread next to the main loop and the phone's voice; faster-whisper
    decodes lazily, so the segments are read inside the lock."""

    def __init__(self, model) -> None:
        self.model, self.lock = model, threading.Lock()

    def transcribe(self, *a, **k):
        with self.lock:
            segs, info = self.model.transcribe(*a, **k)
            return list(segs), info

    def __getattr__(self, name):
        return getattr(self.model, name)


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
