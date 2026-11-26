"""Voice interface: speak to Athena, it speaks back.

Speech-to-text is faster-whisper running on your RTX 4060.
Text-to-speech is the Windows SAPI voice via pyttsx3, so nothing extra downloads.

Recording is energy-gated: it starts capturing when you speak and stops
after a short silence; holding Right Alt records exactly while it's down.

Split on 31 Oct 2026 - this file is the listen loop and run(); the rest is in
    voice_text.py     what was said: wake word, mishearings, follow-ups, echoes
    voice_mic.py      the microphone, hold-to-talk, speech detector, speakers' meter
    voice_speaker.py  how she speaks: the Speaker, instant clips, chimes
    voice_boot.py     start-up: Whisper, wake engines, the primer, background watchers
    voice_hud.py      the HUD window and the orb
Everything is imported back here, so voice.<name> still works. Over 800 lines on
purpose: _voice_loop is one ~780-line function whose nested helpers (_barge_in,
confirm) share its state; cutting it up would trade one long read for subtle bugs.
"""
from __future__ import annotations

import re
import sys
import threading
import time

import numpy as np

from .. import live, offer, presence, wakedata
from ..agent import Agent
from ..config import cfg
from ..tools import scheduler
from .voice_text import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    WAKE_ALIASES,
    _WAKE_LEAD_IN,
    _COMMANDISH,
    _JOINERS,
    _FOLLOW_CMD,
    _MEDIA_TALK,
    _followish,
    _reply_ok,
    _clear_speech,
    _ABOUT,
    _FILLER_ONLY,
    _trusted_text,
    _echoes_prompt,
    _from_last_name,
    _wake_word,
    _wake_probability,
    _REAL_WORDS,
    _strip_wake,
    _HALLUCINATIONS,
    _FILLER,
    fix_mishearings,
    _squash,
    is_hallucination,
    _MEDIA_CMD,
    _WORD,
    _unsaid,
)
from . import voice_hud
from .voice_hud import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _hud_open,
    _hud_hwnd,
    _show_hud,
    _orb_state,
    _orb_level,
)
from .voice_mic import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    SAMPLE_RATE,
    BLOCK,
    MIN_SPEECH_S,
    MAX_UTTERANCE_S,
    HOLD_MAX_S,
    MENU_MASK,
    PARTIAL_BLOCKS,
    Listener,
    _watch_hold_key,
    _Playback,
    _HEADPHONES,
    _OneAtATime,
    _VAD,
    _vad,
    _silence_after_speech,
    _has_speech,
    _level,
)
from .voice_speaker import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    CLIPS,
    _for_speech,
    _breathe,
    Speaker,
    _EARCON,
    earcon,
    chime,
)
from .voice_boot import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _last_wake,
    _on_wake,
    _watch_wake,
    _clock_watch,
    _power_events,
    _BASE_BIAS,
    build_prompt_bias,
    _keep_warm,
    _OpenWakeWord,
    _porcupine,
    _register_cuda_dlls,
    _load_whisper,
    _start_wake_shadow,
    _start_stage,
)

CONFIRM_TIMEOUT = 12.0  # how long to wait for a yes/no before giving up


# True only once Athena has actually been addressed. The orb stays hidden for
# ordinary noise and conversation, and appears only when it is being spoken to.
_ENGAGED = False


def _engage(on: bool) -> None:
    global _ENGAGED
    _ENGAGED = on
    if not on:
        _orb_state("idle")


PROMPT_BIAS = _BASE_BIAS  # replaced at startup by build_prompt_bias()
MULTILINGUAL = False      # set at startup: a model without .en hears Hindi too
_JUST_NAME = "<athena: name only>"  # _barge_in: the name alone was said while Athena was busy


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


class _Partials:
    """The words so far while the key is held, for the Stage (24 Oct 2026,
    Hedwig shows them as you speak). Only the newest audio is read - a pass
    that would wait behind another is skipped, and nothing read after you let
    go is shown."""

    def __init__(self, stt) -> None:
        self.stt, self.audio, self.gen = stt, None, 0
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="stage-partials").start()

    def feed(self, audio) -> None:
        self.audio = (self.gen, audio)
        self.wake.set()

    def stop(self) -> None:
        self.gen += 1
        self.audio = None

    def _loop(self) -> None:
        from .. import stage
        while True:
            self.wake.wait()
            self.wake.clear()
            job, self.audio = self.audio, None
            if not job or job[0] != self.gen or len(job[1]) < SAMPLE_RATE * 0.5:
                continue
            if not self.stt.lock.acquire(blocking=False):      # the final read is running: it wins
                continue
            try:
                segs, _ = self.stt.model.transcribe(_level(job[1]), language="en", beam_size=1, initial_prompt=PROMPT_BIAS,
                                                    condition_on_previous_text=False, without_timestamps=True)
                text = fix_mishearings(" ".join(s.text for s in segs).strip())
            except Exception:
                text = ""
            finally:
                self.stt.lock.release()
            if text and job[0] == self.gen and not is_hallucination(text):
                stage.partial(text)


# ---------------------------------------------------------------- speech detector
# Silero VAD, as shipped inside faster-whisper - no extra package. It tells a
# voice from a fan, music, a cough or a keyboard, which a loudness threshold
# cannot, so a sentence ends when the voice stops (about 0.45 s, instead of
# 0.65 s of quiet), and noise never reaches Whisper at all.


def _continuation(stt, audio, wake: str) -> str:
    """What came right after a bare "Athena" said over her: the command, or ""
    when it was noise, her own voice, or Whisper's primer."""
    from ..agent import clean_speech
    segs, _ = stt.transcribe(audio, language="en", beam_size=5, initial_prompt=PROMPT_BIAS,
                             condition_on_previous_text=False, without_timestamps=True)
    segs = list(segs)
    text = clean_speech(fix_mishearings(" ".join(s.text for s in segs).strip()))
    if wake:
        named, rest = _strip_wake(text, wake, False)
        text = rest if named else text
    text = re.sub(r"^'s\b\s*", "", text).strip(" .,!?")      # "Athena's ..." heard for "Athena, ..."
    if (not text or is_hallucination(text) or not _clear_speech(segs)
            or _echoes_prompt(text, segs, len(audio) / SAMPLE_RATE, PROMPT_BIAS)):
        return ""
    return text


def _serve_phone_voice(stt, speaker) -> None:
    """Lend this process's warm models to the phone app (jarvis/phonevoice.py):
    the same Whisper prompt and fixes as the desk mic, the same Kokoro voice
    and per-sentence tone as Athena speaking here."""
    import io

    from .. import phonevoice

    def hear(raw: bytes) -> str:
        from faster_whisper.audio import decode_audio
        audio = decode_audio(io.BytesIO(raw), sampling_rate=SAMPLE_RATE)
        segs, _ = stt.transcribe(audio, language="en", beam_size=5, initial_prompt=PROMPT_BIAS,
                                 condition_on_previous_text=False, without_timestamps=True)
        return fix_mishearings(" ".join(s.text for s in segs).strip())

    def say(text: str):
        eng = speaker._neural
        if eng is None:
            raise RuntimeError("Athena's voice is still loading")
        from .. import tts as _tts
        parts = [p for p in re.split(r"(?<=[.!?])\s+", _for_speech(text)) if p.strip()] or [text]
        if getattr(eng, "speaks_hindi", False):        # Kokoro: each sentence in its own tone
            pieces = [eng.synth(p, mood=_tts.mood_of(p)) for p in parts]
        else:
            pieces = [eng.synth(p) for p in parts]
        return np.concatenate(pieces), eng.sample_rate

    phonevoice.serve(hear, say)


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
        stt = _OneAtATime(_load_whisper(model_size, device))
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

    from .. import stage

    def _stepped(s) -> None:
        print(f"   -> {s.tool}")
        live.step(s.tool)
        try:
            stage.on_step(s.tool, s.args or {}, s.result or "")     # its card, the moment it's done
        except Exception as e:
            print(f"[stage card: {e}]")
    agent = Agent(on_step=_stepped, confirm=confirm_aloud, voice=True)

    ok, msg = agent.llm.available()
    if not ok:
        print(msg)
        return

    # Away and back, "sorry to interrupt", welcome-backs (presence.py).
    presence.set_speaker(speaker.say, speaker.busy)
    playback = _Playback(speaker)
    presence.set_playing(lambda: playback.recent(20))      # no welcome-back over a class or a film
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
        from .. import goals
        goals.start(presence.interject)           # long-running goals: re-plan, nudge, wrap up
        from .. import missions
        missions.start_watch(presence.interject)  # say when a Claude mission finishes
        from .. import attention, loops
        attention.start_watch(presence.interject)  # say when a Deck console is stuck or asks you something
        loops.start(presence.interject)           # repeat jobs until their goal is met, then say so
        _serve_phone_voice(stt, speaker)          # the phone app hears and speaks through these models
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
    try:
        from .. import mcp             # plug-in tool servers (config.yaml mcp.servers), in the background
        mcp.start_all()
    except Exception as e:
        print(f"[mcp: not started ({e})]")

    # The profile learner, for the same reason: voice is always running. It
    # only works while Athena is idle, so it never holds up a reply.
    try:
        from .. import profile
        profile.start_learner(is_idle=lambda: live._state.get("state") == "idle",
                              log=live.note)
    except Exception as e:
        print(f"[memory learner unavailable: {e}]")

    hint = f" Say '{wake}' first to address me." if wake else ""
    hold = _watch_hold_key(listener, speaker)
    if hold:
        hint += f" Or hold {hold.title()} and talk."
    partials = _Partials(stt)
    listener.on_partial = partials.feed
    if cfg.get("stage.enabled", True):
        _start_stage()
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

    def _name_only() -> None:
        """Her name alone: she listens on, and the Stage shows it. She said "Yes?" out loud
        until 28 Oct 2026 - 72 times, many of them to typing or a cough that Whisper, primed
        for her name, heard as "Athena?". voice.name_reply: yes brings it back."""
        if cfg.get("voice.name_reply", "listen") == "yes":
            _orb_state("speaking")
            speaker.say("Yes?")
            speaker.wait_until_done(timeout=20)
            listener.drain()
        _orb_state("listening")

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
        if not listener.pushed and playback.during(time.time() - len(audio) / SAMPLE_RATE - 0.5, time.time()):
            return None                      # the speakers, not you (a class, a video): only a stop gets through, above
        if listener.pushed:                  # said with the key held: meant for her, name or not
            rest = re.sub(rf"^\W*(?:{re.escape(wake or 'athena')})\b\W*", "", heard, flags=re.I).strip(" .!?,")
        elif wake:
            m = re.search(rf"\b(?:{re.escape(wake)}|athina|atheena|athene|atena|thena|athenas)\b"
                          rf"[\s,.!?]*(.*)$", heard, re.I)
            if not m:
                return None
            rest = re.sub(r"^'s\b\s*", "", m.group(1)).strip(" .!?,")    # "Athena's on" for "Athena, it's on"
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
                            _name_only()
                            invited = True
                        else:
                            _engage(False)
                        continue
                else:
                    audio = listener.record_utterance(timeout, max_wait=wait_cap)
                    if audio is None:
                        continue
                t_stop = time.time() - listener.last_wait     # you stopped talking about here
                if listener.pushed:                            # let go of the key: she's on it, show it at once
                    partials.stop()
                    _engage(True)
                    _orb_state("thinking")
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
                raw = audio                                    # as the mic heard it, for the wake-word data
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
                stage.partial("")                              # the final words replace the live ones
                if hindi_turn:
                    from .. import hindi
                    text = hindi.wake_to_latin(text)
                # No name anywhere in it (a lecture, the TV, typing taken for
                # speech): what "not Athena" sounds like here (wakedata.py).
                if wake and not _strip_wake(text, wake, at_start=False)[0]:
                    wakedata.negative(raw)
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
                    # A near-name ("Artha") has no score of its own and used to skip
                    # this check entirely; it now counts as unsure (Hindi aside: its
                    # words are Devanagari, the name was converted after).
                    if p is None and not hindi_turn:
                        p = 0.0
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
                tapped = live.take_wake() or listener.pushed     # the key held down counts too
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
                if still_awake and not heard_wake and not tapped:
                    if not (_reply_ok(text) if invited else _followish(text)) or not _clear_speech(segments):
                        still_awake = False
                        if log_ignored:
                            print("   [follow-up ignored: not a clear request]")
                # Not for an answer: "all" is mostly Athena's own "...a number,
                # or all." and would be thrown away as her echo.
                if still_awake and not heard_wake and not tapped and last_said["a"] \
                        and not answering and _echo(text, last_said["a"]):
                    still_awake = False
                addressed = (not wake) or heard_wake or still_awake
                # The speakers were playing while this was said - a class, a video, music: it's
                # most likely them, so only the key or a short control ("Athena, pause") counts.
                if addressed and not tapped and t_stop and playback.during(t_stop - len(audio) / SAMPLE_RATE - 0.3, t_stop) \
                        and not _MEDIA_CMD.match(remainder if heard_wake else text):
                    print(f"   [ignored: {text[:80]!r} - the speakers were playing (a class, a video, music);"
                          " hold Right Alt to talk over them]")
                    addressed = False
                if addressed:
                    invited = False
                    if heard_wake and (w := _wake_word(segments)) is not None:
                        wakedata.positive(raw, float(w.end))   # a real "Athena", from Whisper's word timing
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
                            _name_only()
                            invited = True
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
            # Spoken conversations are kept as chats too, like the HUD's: a new
            # one after chat.new_after_hours of quiet (memory.stale).
            if agent.memory.chat_id is None or agent.memory.stale():
                agent.memory.start_chat()
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
                card = None if stage.shown_since(t_ask) else stage.answer_card(text, answer)
                if card:                                       # a long answer: to read as well as hear
                    stage.show(card)
                _orb_state("speaking")
                done_aloud = agent.spoken.strip()
                if not done_aloud:
                    speaker.say(answer, hindi=reply_hindi)
                else:
                    # The streamed part was cut short (a guard stopped it) or
                    # the answer changed: say only what has not been said.
                    rest = _unsaid(answer, done_aloud)
                    if rest:
                        speaker.say(rest, hindi=reply_hindi)
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
                    # "Athena, [pause] set the volume to fifty": the barge-in caught
                    # only the name, and the drain below threw the command away -
                    # "Yes?" twice to real commands on 16 Oct 2026. Listen a
                    # moment first; only if nothing follows is it a bare "Athena".
                    more = listener.record_utterance(timeout, max_wait=1.6)
                    rest = _continuation(stt, more, wake) if more is not None else ""
                    if rest:
                        print(f"   [the rest after the name: {rest!r}]")
                        live.note(f"interrupted - new command: {rest[:60]}")
                        queued = rest
                        _orb_state("thinking")
                        continue
                    time.sleep(0.2)
                    listener.drain()
                    earcon()
                    _name_only()
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
    # The Stage has its own mascot; the old orb would be a second one.
    orb = None
    if cfg.get("overlay.enabled", True) and not cfg.get("stage.enabled", True):
        try:
            from .overlay import Orb

            orb = Orb(
                size=int(cfg.get("overlay.size", 200)),
                position=cfg.get("overlay.position", "center"),
                hide_when_idle=bool(cfg.get("overlay.hide_when_idle", False)),
            )
        except Exception as e:
            print(f"[overlay unavailable: {e} - continuing without it]")
            orb = None
    voice_hud._ORB = orb                   # _orb_state / _orb_level (voice_hud.py) drive it

    if orb is None:
        _voice_loop()
        return

    def worker() -> None:
        try:
            _voice_loop()
        finally:
            orb.stop()

    threading.Thread(target=worker, daemon=True, name="jarvis-voice").start()
    try:
        orb.run()
    except KeyboardInterrupt:
        orb.stop()


if __name__ == "__main__":
    sys.exit(run())
