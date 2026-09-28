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

# Above this many seconds of work per second of speech the first word comes
# too late to feel instant; a slower engine than that is passed over in auto.
MAX_REALTIME_FACTOR = 0.5


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

    def synth(self, text: str) -> np.ndarray:
        audio, sr = self.k.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        self.sample_rate = sr
        return np.asarray(audio, dtype=np.float32)


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
            self.syn = SynthesisConfig(length_scale=1.0 / max(0.5, speed))
        except Exception:
            self.syn = None

    def synth(self, text: str) -> np.ndarray:
        chunks = list(self.v.synthesize(text, self.syn) if self.syn else self.v.synthesize(text))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)


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
                gpu = _cuda_ready() and (want == "kokoro" or _free_vram_gb() >= 1.2)
                if want == "auto" and not gpu:
                    tried.append("kokoro: no GPU room (needs onnxruntime-gpu and ~1 GB free)")
                    continue
                engine: Engine = Kokoro("cuda" if gpu else "cpu")
            else:
                if not piper_installed():
                    tried.append("piper: no voice downloaded (python run.py voice-setup piper)")
                    continue
                engine = Piper()
            rtf = _timed(engine)
            where = "GPU" if getattr(engine, "device", "cpu") == "cuda" else "CPU"
            if want == "auto" and rtf > MAX_REALTIME_FACTOR:
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
