"""Start-up and background: loading Whisper (and CUDA's DLLs), the wake-word engines,
the Whisper primer, keeping the models warm, and the watchers for waking from sleep,
the clock and power events.

Split out of voice.py (31 Oct 2026); voice.py imports all of it back.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np

from .. import live, presence
from ..config import cfg
from .voice_hud import _show_hud
from .voice_mic import SAMPLE_RATE


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
    # No "set the volume to fifty": plain words need no priming, and it was the
    # line Whisper handed back most for noise - 29 phantom volume commands by
    # 28 Oct 2026, every one caught, but a real one said alike was doubted too.
    "Athena, lock my screen. "
    "Athena, find my PDFs. Athena, what is the weather? "
    # Without these, "call" comes back as "oll" or "all" and the command is
    # lost before anything can act on it.
    "Athena, call Maa. Athena, video call Papa. Athena, open Claude. "
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

    # Athena's own NVIDIA libraries (pip) first, Ollama's last: an Ollama
    # update swapped its CUDA 13 files for CUDA 12 on 14 Oct 2026 and the voice
    # (onnxruntime) fell to the CPU, because it had been borrowing them.
    roots: list[Path] = []
    packages = list(site.getsitepackages())
    user_site = site.getusersitepackages()
    packages += [user_site] if isinstance(user_site, str) else list(user_site)
    for base in packages:
        nvidia = Path(base) / "nvidia"
        if nvidia.is_dir():
            roots += sorted(nvidia.glob("*/bin"))
    roots += [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
              / "lib" / "ollama" / sub for sub in ("cuda_v12", "cuda_v13")]

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


def _start_stage() -> None:
    """The Stage's window (run.py stage, ui/stage_window.py) - one copy; run.py's mutex
    turns a second start away."""
    import subprocess
    root = Path(__file__).resolve().parents[2]
    exe = Path(sys.executable).with_name("pythonw.exe")
    try:
        subprocess.Popen([str(exe if exe.exists() else sys.executable), "run.py", "stage"], cwd=str(root),
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        print(f"[stage: not started ({e})]")
