"""Athena speaking in a chosen voice, cloned from a short sample.

Coqui XTTS-v2 takes a few seconds of someone speaking (data/voice_ref.wav,
made from the clip you supplied) and speaks any text in that voice, locally.
The speaker "embedding" is worked out once from the sample and reused, so
each sentence costs only the synthesis.

Latency is the cost. SAPI starts talking instantly; XTTS has to generate the
audio first. So a reply is split into sentences and each is played while the
next is being generated - the first words arrive after one sentence's worth
of work, not the whole reply's.

GPU if there is room, else CPU. The everyday model and Whisper already sit
in most of the 8 GB, and XTTS wants about 2 GB, so it checks free VRAM at
load time rather than evicting the model every reply depends on.

If anything here fails - not installed, no sample, out of memory - Speaker
falls back to the Windows voice. Athena never goes silent over this.
"""
from __future__ import annotations

import re
import threading

import numpy as np

from .config import DATA, cfg

REF = DATA / "voice_ref.wav"
SAMPLE_RATE = 24000

_lock = threading.Lock()
_model = None
_latents = None
_device = "cpu"


def available() -> bool:
    if not REF.exists():
        return False
    try:
        import TTS  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def _free_vram_gb() -> float:
    try:
        import torch
        if not torch.cuda.is_available():
            return 0.0
        free, _total = torch.cuda.mem_get_info()
        return free / 1024**3
    except Exception:
        return 0.0


def load() -> str:
    """Load XTTS and the speaker from the sample. Returns where it runs."""
    global _model, _latents, _device
    with _lock:
        if _model is not None:
            return _device
        import os
        os.environ.setdefault("COQUI_TOS_AGREED", "1")   # XTTS licence: non-commercial use
        import torch
        from TTS.api import TTS

        want = cfg.get("voice.clone_device", "auto")
        _device = ("cuda" if want == "cuda" or (want == "auto" and _free_vram_gb() > 2.6)
                   else "cpu")
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(_device)
        _model = tts.synthesizer.tts_model
        _latents = _model.get_conditioning_latents(audio_path=[str(REF)])
        return _device


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for p in parts:
        # XTTS degrades past ~250 characters per call; cut long ones at commas.
        while len(p) > 220:
            cut = p.rfind(",", 0, 220)
            cut = cut if cut > 60 else 220
            out.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            out.append(p)
    return out


def synth(sentence: str) -> np.ndarray:
    gpt, spk = _latents
    speed = float(cfg.get("voice.clone_speed", 0.9))
    with _lock:
        out = _model.inference(sentence, "en", gpt, spk, speed=speed,
                               temperature=0.65, enable_text_splitting=False)
    return np.asarray(out["wav"], dtype=np.float32)


def speak(text: str, stop: threading.Event | None = None) -> None:
    """Say `text`, generating the next sentence while this one plays."""
    import queue

    import sounddevice as sd

    load()
    chunks: queue.Queue = queue.Queue(maxsize=2)

    def producer():
        for s in _sentences(text):
            if stop is not None and stop.is_set():
                break
            chunks.put(synth(s))
        chunks.put(None)

    threading.Thread(target=producer, daemon=True).start()
    while True:
        audio = chunks.get()
        if audio is None:
            break
        sd.play(audio, SAMPLE_RATE)
        sd.wait()
