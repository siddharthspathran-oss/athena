"""Real "Athena"s and real room sound, kept to retrain the wake word.

The detector trained on synthetic voices failed in the room (16 Oct 2026:
caught 41%, woke 20 times an hour) - it had never heard a lecture, the TV or
typing. The voice loop already has both answers from Whisper's word timings:
where in a command you said "Athena", and which sounds had no name in them at
all. This keeps them for finetune/wakeword.py --real.

Kept as openWakeWord embeddings only (16 x 96 numbers per 1.28 s, the
detector's own input): no audio and no words are written. data/wake_real/,
at most MAX_MB; voice.wake_collect: false stops it. The work runs on its own
thread and is dropped when it falls behind, so listening never waits on it.
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

from .config import DATA, cfg

FOLDER = DATA / "wake_real"
SR = 16000
MAX_MB = 150                  # ~2 hours of room speech; positives are tiny
FRAME_S = 0.08                # one openWakeWord embedding frame
GAPS = 3                      # windows ending 0.08 / 0.16 / 0.24 s after the name, as in training
FLUSH_WINDOWS = 300

_q: queue.Queue = queue.Queue(maxsize=8)
_neg: list[np.ndarray] = []
_started = False


def positive(audio: np.ndarray, word_end: float) -> None:
    """You said "Athena" and it ended word_end seconds into audio."""
    _put(("pos", audio, word_end))


def negative(audio: np.ndarray) -> None:
    """Sound with no "Athena" in it."""
    _put(("neg", audio, 0.0))


def _put(item: tuple) -> None:
    global _started
    if not cfg.get("voice.wake_collect", True):
        return
    if not _started:
        _started = True
        FOLDER.mkdir(exist_ok=True)
        threading.Thread(target=_work, daemon=True, name="wake-data").start()
    try:
        _q.put_nowait(item)
    except queue.Full:
        pass


def _embed(f, audio: np.ndarray) -> np.ndarray:
    """(frames, 96), the way finetune/wakeword.py embeds its clips: at least
    2.5 s, silence in front. A frame needs ~0.78 s of sound before it, so a
    bare "Athena" at the start of a command gave no window at all."""
    lead = max(SR // 2, int(2.5 * SR) - len(audio))
    pad = np.concatenate([np.zeros(lead, np.float32), np.asarray(audio, np.float32)])
    pad = np.pad(pad, (0, -len(pad) % 1280))
    return f.embed_clips((np.clip(pad, -1, 1) * 32767).astype(np.int16)[None, :], batch_size=1)[0]


def windows(f, kind: str, audio: np.ndarray, word_end: float = 0.0) -> np.ndarray:
    if kind == "pos":
        emb = _embed(f, audio[: int((word_end + GAPS * FRAME_S) * SR)])
        n = len(emb)
        return np.stack([emb[n - 16 - k: n - k] for k in range(GAPS)]) if n >= 16 + GAPS else np.empty((0, 16, 96))
    emb = _embed(f, audio)
    return np.stack([emb[j:j + 16] for j in range(0, len(emb) - 15, 2)]) if len(emb) >= 16 else np.empty((0, 16, 96))


def _full() -> bool:
    return sum(p.stat().st_size for p in FOLDER.glob("*.npy")) > MAX_MB * 1e6


def _save(kind: str, w: np.ndarray) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    np.save(FOLDER / f"{kind}-{stamp}.npy", w.astype(np.float16))


def _work() -> None:
    try:
        from openwakeword.utils import AudioFeatures
        f = AudioFeatures(inference_framework="onnx")
    except Exception as e:
        print(f"[wake data: off ({str(e)[:80]})]")
        return
    while True:
        kind, audio, end = _q.get()
        try:
            if _full():
                continue
            w = windows(f, kind, audio, end)
            if not len(w):
                continue
            if kind == "pos":
                _save("pos", w)                  # one file per "Athena", so a test split never splits one
                continue
            _neg.append(w)
            if sum(len(x) for x in _neg) >= FLUSH_WINDOWS:
                _save("neg", np.concatenate(_neg))
                _neg.clear()
        except Exception as e:
            print(f"[wake data: {str(e)[:80]}]")
