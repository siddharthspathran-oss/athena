"""Neural voices for Athena: Kokoro on the GPU, Piper on the CPU.

The Windows SAPI voice is instant but plainly a machine. Two local, free
engines sound like a person instead:

  kokoro  82M parameters, the most natural open voice there is. Fast on the
          GPU (many times real time); too slow on a laptop CPU to feel
          instant - measured 1.7 s per second of speech on a 4-core CPU with
          the int8 model - so it is used on the GPU only unless asked for.
  piper   Less natural than Kokoro, far more natural than SAPI, and faster
          than real time on the CPU. It never touches the GPU, so it never
          competes with the chat model for VRAM.

voice.engine in config.yaml picks: "auto" (Kokoro if it can run on the GPU,
else Piper, else SAPI), or one by name. Models live in data/voices and are
fetched once with `python run.py voice-setup`; with none there, Athena uses
SAPI exactly as before. Any failure at load or while speaking falls back to
SAPI for that sentence - a voice that errors must never mean silence.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

import numpy as np

from .config import DATA, cfg

VOICES = DATA / "voices"

KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = {"gpu": "kokoro-v1.0.onnx", "cpu": "kokoro-v1.0.int8.onnx", "voices": "voices-v1.0.bin"}
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
PIPER_DEFAULT = "en_US-hfc_female-medium"

# Seconds of work per second of speech above which an engine is passed over
# in auto. The first word waits for the whole first sentence to be made, so
# this is the delay per second of that sentence: at 0.2 a two-second sentence
# starts 0.4 s after it is ready, near the Windows voice's own start-up.
MAX_REALTIME_FACTOR = 0.2


class Engine:
    name = "none"
    sample_rate = 24000

    def synth(self, text: str) -> np.ndarray:
        raise NotImplementedError


# ---------------------------------------------------------------- kokoro

def _cuda_ready() -> bool:
    try:
        import onnxruntime as rt
        return "CUDAExecutionProvider" in rt.get_available_providers()
    except Exception:
        return False


def _free_vram_gb() -> float:
    """Free VRAM from nvidia-smi, or 0 if it cannot be read."""
    import subprocess

    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return float(out.splitlines()[0]) / 1024
    except Exception:
        return 0.0


def _gpu_has_room(log=print) -> bool:
    """Room for Kokoro on the card without squeezing the chat model.

    Decided only once the chat model is on the GPU. Voice starts at login
    alongside the model's warm-up; had Kokoro taken its VRAM first, Ollama
    would have fitted the model around it - part on the CPU, every reply
    many times slower, the failure the README warns about. The margin
    (voice.kokoro_min_free_gb) also covers the vision model, which is 0.4 GB
    bigger than the chat model it swaps in for.
    """
    try:
        from .llm import LLM

        llm, deadline = LLM(), time.time() + 90
        where = llm.processor()
        while where in ("not loaded", "unknown") and time.time() < deadline:
            time.sleep(2)
            where = llm.processor()
        if where != "GPU":
            log(f"[voice: chat model is {where} - leaving the GPU to it]")
            return False
    except Exception:
        return False
    return _free_vram_gb() >= float(cfg.get("voice.kokoro_min_free_gb", 1.6))


class Kokoro(Engine):
    name = "kokoro"
    sample_rate = 24000

    def __init__(self, device: str):
        import onnxruntime as rt
        from kokoro_onnx import Kokoro as _K

        model = VOICES / (KOKORO_FILES["gpu"] if device == "cuda" else KOKORO_FILES["cpu"])
        if not model.exists():                       # either model runs anywhere
            model = next(VOICES / f for f in (KOKORO_FILES["gpu"], KOKORO_FILES["cpu"])
                         if (VOICES / f).exists())
        opts = rt.SessionOptions()
        opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)   # leave cores for Whisper
        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"] if device == "cuda"
                     else ["CPUExecutionProvider"])
        session = rt.InferenceSession(str(model), sess_options=opts, providers=providers)
        self.device = "cuda" if "CUDAExecutionProvider" in session.get_providers() else "cpu"
        self.k = _K.from_session(session, str(VOICES / KOKORO_FILES["voices"]))
        self.voice = str(cfg.get("voice.kokoro_voice", "af_heart"))
        self.speed = float(cfg.get("voice.tts_speed", 1.05))

    speaks_hindi = True

    def _style(self, mood: str):
        """The voice for a mood: your voice, blended a little toward another
        one for colour (Kokoro styles mix linearly). Cached per mood."""
        if not hasattr(self, "_styles"):
            self._styles = {}
        if mood not in self._styles:
            style = self.k.get_voice_style(self.voice)
            for other, w in MOODS.get(mood, MOODS["neutral"])[2].items():
                try:
                    style = (1 - w) * style + w * self.k.get_voice_style(other)
                except Exception:
                    pass
            self._styles[mood] = style
        return self._styles[mood]

    def synth(self, text: str, hindi: bool = False, mood: str = "neutral") -> np.ndarray:
        # bf_/bm_ voices are British: British pronunciation too, not just the accent.
        voice, lang = self.voice, "en-gb" if self.voice[:1] == "b" else "en-us"
        if hindi:
            voice, lang = str(cfg.get("language.hindi_voice", "hf_alpha")), "hi"
        if hindi or not cfg.get("voice.expression", True) or mood not in MOODS:
            mood = "neutral"
        pace, semitones, _ = MOODS[mood]
        shift = 2 ** (semitones / 12)
        # Pitch up by speaking slower, then playing back faster by the same
        # factor: higher voice, same timing - no extra library needed.
        audio, sr = self.k.create(text, voice=voice if mood == "neutral" or hindi else self._style(mood),
                                  speed=self.speed * pace / shift, lang=lang)
        self.sample_rate = sr
        audio = np.asarray(audio, dtype=np.float32)
        if abs(shift - 1) > 1e-3 and len(audio) > 1:
            n = max(2, int(len(audio) / shift))
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        return audio


# ---------------------------------------------------------------- expression
# Kokoro has no emotion control, so a mood is pace, pitch and a little of a
# second voice blended in (asked for 4 Oct 2026: "a questioning tone, a
# joyful tone"). (pace x, pitch in semitones, {voice: share}).
MOODS = {
    "neutral":  (1.00, 0.0, {}),
    "question": (0.97, 0.6, {}),                        # Kokoro itself lifts the end at a "?"
    "happy":    (1.05, 1.0, {"bf_isabella": 0.30}),
    "excited":  (1.09, 1.6, {"bf_isabella": 0.40}),
    "sorry":    (0.91, -0.8, {"bf_lily": 0.30}),
}
_HAPPY = re.compile(r"\b(?:done|great|awesome|nice|welcome back|good (?:morning|afternoon|evening)|congrat\w*|yay|"
                    r"perfect|you'?re welcome|sure thing|on it|got it|playing|here you go|there you are|well done|"
                    r"no problem|happy|love|ready|all set|hey)\b", re.I)
_SORRY = re.compile(r"\b(?:sorry|couldn'?t|can'?t|cannot|unfortunately|failed|missed|overdue|didn'?t|not able|"
                    r"afraid|problem|error|unable|lost|no luck|signed out|expired)\b", re.I)


def mood_of(text: str) -> str:
    """The mood of one sentence, from its words and its last mark."""
    t = text.strip()
    if _SORRY.search(t):
        return "sorry"
    if t.endswith("?"):
        return "question"
    if t.endswith("!"):
        return "excited" if _HAPPY.search(t) else "happy"
    return "happy" if _HAPPY.search(t) else "neutral"


def kokoro_installed() -> bool:
    return ((VOICES / KOKORO_FILES["voices"]).exists()
            and any((VOICES / f).exists() for f in (KOKORO_FILES["gpu"], KOKORO_FILES["cpu"])))


# ---------------------------------------------------------------- piper

class Piper(Engine):
    name = "piper"

    def __init__(self):
        from piper import PiperVoice

        name = str(cfg.get("voice.piper_voice", PIPER_DEFAULT))
        model = VOICES / f"{name}.onnx"
        self.v = PiperVoice.load(str(model), config_path=str(model) + ".json", use_cuda=False)
        self.sample_rate = int(self.v.config.sample_rate)
        speed = float(cfg.get("voice.tts_speed", 1.05))
        try:
            from piper import SynthesisConfig
            # noise_w_scale: how much word lengths vary. Above the voice's
            # 0.8 the rhythm is less even - more like a person, less a metronome.
            self.syn = SynthesisConfig(length_scale=1.0 / max(0.5, speed),
                                       noise_w_scale=float(cfg.get("voice.rhythm", 0.95)))
        except Exception:
            self.syn = None

    def synth(self, text: str) -> np.ndarray:
        chunks = list(self.v.synthesize(text, self.syn) if self.syn else self.v.synthesize(text))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        # Piper hands back one chunk per sentence with no gap between them.
        gap = np.zeros(int(float(cfg.get("voice.sentence_pause", 0.32)) * self.sample_rate), dtype=np.float32)
        parts = []
        for c in chunks:
            parts += [c.audio_float_array.astype(np.float32), gap]
        return np.concatenate(parts[:-1])


def piper_installed() -> bool:
    name = str(cfg.get("voice.piper_voice", PIPER_DEFAULT))
    return (VOICES / f"{name}.onnx").exists() and (VOICES / f"{name}.onnx.json").exists()


# ---------------------------------------------------------------- choosing

def _timed(engine: Engine) -> float:
    """Seconds of work per second of speech, measured on a real sentence
    (the first call also pays for warm-up, so the second one is timed)."""
    engine.synth("Ready.")
    t = time.time()
    audio = engine.synth("Opened your notes, from Documents.")
    return (time.time() - t) / max(len(audio) / engine.sample_rate, 0.1)


def load(log=print) -> Engine | None:
    """The best voice this PC can run fast enough, or None for SAPI."""
    want = str(cfg.get("voice.engine", "auto")).lower()
    if want not in ("auto", "kokoro", "piper"):
        return None
    tried = []
    order = ["kokoro", "piper"] if want in ("auto", "kokoro") else ["piper"]
    for name in order:
        try:
            if name == "kokoro":
                if not kokoro_installed():
                    tried.append("kokoro: not downloaded (python run.py voice-setup)")
                    continue
                gpu = _cuda_ready() and (want == "kokoro" or _gpu_has_room(log))
                if want == "auto" and not gpu:
                    tried.append("kokoro: no GPU room next to the chat model (needs onnxruntime-gpu "
                                 "and voice.kokoro_min_free_gb free)")
                    continue
                engine: Engine = Kokoro("cuda" if gpu else "cpu")
            else:
                if not piper_installed():
                    tried.append("piper: no voice downloaded (python run.py voice-setup piper)")
                    continue
                engine = Piper()
            rtf = _timed(engine)
            if rtf > float(cfg.get("voice.max_realtime_factor", MAX_REALTIME_FACTOR)):
                # At start-up Whisper is warming up on the same GPU; one slow
                # reading passed Kokoro over for Piper (3 Oct 2026). Time again.
                time.sleep(3)
                rtf = min(rtf, _timed(engine), _timed(engine))
            where = "GPU" if getattr(engine, "device", "cpu") == "cuda" else "CPU"
            if want == "auto" and rtf > float(cfg.get("voice.max_realtime_factor", MAX_REALTIME_FACTOR)):
                tried.append(f"{name}: too slow here ({rtf:.2f}s per second of speech)")
                continue
            log(f"[voice: {name} on the {where}, {rtf:.2f}s per second of speech]")
            return engine
        except Exception as e:
            tried.append(f"{name}: {str(e)[:100]}")
    if tried:
        log("[voice: Windows voice - " + "; ".join(tried) + "]")
    return None


# ---------------------------------------------------------------- setup

def _fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have {dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  downloading {dest.name} ...", end="", flush=True)
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got, last = 0, 0.0
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
            got += len(block)
            if total and time.time() - last > 1:
                last = time.time()
                print(f"\r  downloading {dest.name} ... {got * 100 // total}%", end="", flush=True)
    tmp.replace(dest)
    print(f"\r  downloaded {dest.name} ({got / 1e6:.0f} MB)        ")


def setup(which: str = "auto") -> int:
    """`python run.py voice-setup [kokoro|piper|all]`: fetch the voice files.

    auto: Kokoro (both models - the full one for the GPU, int8 for the CPU)
    plus a Piper voice as the CPU fallback. About 450 MB in all.
    """
    which = (which or "auto").lower()
    print(f"Voice files go in {VOICES}.\n")
    ok = True
    if which in ("auto", "all", "kokoro"):
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            print("Kokoro needs:  pip install kokoro-onnx")
            print("  and for the GPU:  pip install onnxruntime-gpu\n")
            ok = False
        for f in KOKORO_FILES.values():
            try:
                _fetch(KOKORO_URL + f, VOICES / f)
            except Exception as e:
                print(f"\n  could not fetch {f}: {e}")
                ok = False
    if which in ("auto", "all", "piper"):
        try:
            import piper  # noqa: F401
        except ImportError:
            print("Piper needs:  pip install piper-tts\n")
            ok = False
        name = str(cfg.get("voice.piper_voice", PIPER_DEFAULT))
        lang, rest = name.split("-", 1)
        speaker, quality = rest.rsplit("-", 1)
        base = f"{PIPER_URL}{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{name}.onnx"
        for url, dest in ((base, VOICES / f"{name}.onnx"), (base + ".json", VOICES / f"{name}.onnx.json")):
            try:
                _fetch(url, dest)
            except Exception as e:
                print(f"\n  could not fetch {dest.name}: {e}")
                ok = False
    print("\nChecking which voice this PC can run quickly...")
    engine = load()
    if engine is None:
        print("Athena will keep the Windows voice for now.")
    else:
        print(f"Athena will speak with {engine.name}. Restart voice to hear it:  python run.py voice")
    return 0 if ok else 1


def status() -> dict:
    return {"kokoro": kokoro_installed(), "piper": piper_installed(),
            "gpu": _cuda_ready(), "engine": cfg.get("voice.engine", "auto")}


if __name__ == "__main__":
    print(json.dumps(status(), indent=2))
