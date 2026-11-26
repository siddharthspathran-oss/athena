"""How Athena speaks: the Speaker (Kokoro / Piper / Windows voices, sentence by sentence,
interruptible), the instant clips, and the small sounds (earcon, chimes).

Split out of voice.py (31 Oct 2026); voice.py imports all of it back.
"""
from __future__ import annotations

import queue
import re
import threading
import time

import numpy as np

from ..config import cfg


# Short lines said again and again before acting (agent._acknowledge, routes._SLOW_TOOLS):
# synthesised once at start-up, then played with no wait.
CLIPS = ("Yes?", "On it.", "Opening it.", "Sending it.", "Calling.", "Ringing your phone.",
         "Bringing up your phone.", "Okay, I'll leave it.", "Going quiet.")


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
        self._clips: dict[str, np.ndarray] = {}          # CLIPS, made once the voice is loaded
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
            if self._neural is not None and text in self._clips:     # said many times: made once, played at once
                self._play_q.put((self._gen, text, self._clips[text], self._neural.sample_rate))
                continue
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
        if engine is not None:
            # The short lines she says before acting ("On it.", "Opening it."), made now so
            # they play the instant they're needed instead of after a synthesis (24 Oct 2026).
            for line in CLIPS:
                try:
                    self._clips[line] = _breathe(engine.synth(_for_speech(line)), line, engine.sample_rate)
                except Exception:
                    break

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
