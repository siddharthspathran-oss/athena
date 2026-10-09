"""Telling your voice apart from Athena's own, by the sound itself.

The laptop's microphone hears Athena's speaker. Until now that was caught by
comparing words ("is this transcript mostly what she just said?"), which only
works once Whisper has transcribed it - and a misheard echo of her own voice
still stopped her mid-sentence or got answered as a command (4 Oct 2026).

This compares sound instead. The idea is from Mark LV's core/echo.py
(FatihMakes, CC BY-NC 4.0); the method here was rebuilt and measured on real
voices, because the original - match each microphone slice against any slice
played in the last 1.5 s - could not tell a voice from echo with Kokoro's
voices (both left ~0.13-0.19 unexplained):

  1. What Athena played and what the microphone heard are cut into 32 ms
     frames, each reduced to energies in 16 speech bands.
  2. The delay from speaker to microphone is found by lining up the two
     loudness envelopes (it is fixed for the laptop, tens of milliseconds).
  3. Each microphone frame has as much as fits of the frame Athena was
     playing at that moment (+-1 frame) subtracted. Her echo cancels; a
     person's voice, with its energy in other bands, does not.
  4. A voice is talk that LASTS: the median over a quarter-second window.

Measured (Emma as Athena, three other voices talking over her): echo-only
windows peaked at 0.25-0.30, windows with a voice at 0.38-1.0. The threshold
starts at 0.34 and learns from the room, staying just above its echo.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

SR = 16000                          # the microphone's rate; playback is resampled to it
_FRAME = int(0.032 * SR)            # 32 ms
_EDGES = np.geomspace(150, 7000, 17)
_WINDOW = 8                         # frames in a decision window (~0.26 s)
_MAX_LAG = 40                       # frames of speaker-to-mic delay searched (~1.3 s)
_MIN_LEVEL = 0.01                   # mic frames quieter than this are room noise


def _bands(x: np.ndarray) -> np.ndarray:
    x = x - x.mean()
    n = 1 << (len(x) - 1).bit_length()
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=n))
    f = np.fft.rfftfreq(n, 1.0 / SR)
    return np.array([mag[(f >= a) & (f < b)].sum() for a, b in zip(_EDGES[:-1], _EDGES[1:])])


def _frames(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    idx = range(0, max(0, len(x) - _FRAME), _FRAME)
    return (np.array([_bands(x[i:i + _FRAME]) for i in idx]).reshape(-1, len(_EDGES) - 1),
            np.array([np.sqrt(np.mean(x[i:i + _FRAME] ** 2)) for i in idx]))


class Echo:
    def __init__(self) -> None:
        self._clips: deque = deque()                 # (start, audio at 16 kHz)
        self._lock = threading.Lock()
        self._peaks: deque = deque(maxlen=40)        # highest window of recordings judged echo
        self.threshold = 0.30                        # below: her echo; above +0.06: a voice

    def played(self, audio: np.ndarray, rate: int, start: float | None = None) -> None:
        """Note a clip Athena is about to play (start: its time.monotonic())."""
        start = time.monotonic() if start is None else start
        a = np.asarray(audio, dtype=np.float32)
        if rate != SR and len(a) > 1:
            a = np.interp(np.arange(0, len(a), rate / SR), np.arange(len(a)), a).astype(np.float32)
        with self._lock:
            self._clips.append((start, a))
            while self._clips and self._clips[0][0] + len(self._clips[0][1]) / SR < time.monotonic() - 12:
                self._clips.popleft()

    def _timeline(self, t0: float, t1: float) -> np.ndarray:
        """What Athena played between t0 and t1, as one signal (silence between clips)."""
        out = np.zeros(max(0, int((t1 - t0) * SR)), dtype=np.float32)
        with self._lock:
            clips = list(self._clips)
        for start, a in clips:
            i = int((start - t0) * SR)
            lo, hi = max(0, i), min(len(out), i + len(a))
            if hi > lo:
                out[lo:hi] += a[lo - i:hi - i]
        return out

    def has_voice(self, mic: np.ndarray, end: float | None = None) -> bool | None:
        """For a 16 kHz microphone recording that ended at `end` (time.monotonic()):
        True - someone is speaking in it besides Athena; False - only her echo;
        None - she was not playing then, so there is nothing to compare."""
        end = time.monotonic() if end is None else end
        mic = np.asarray(mic, dtype=np.float32).ravel()
        start = end - len(mic) / SR
        lead = _MAX_LAG * _FRAME / SR
        played = self._timeline(start - lead, end + 0.3)
        if not len(played) or np.sqrt(np.mean(played ** 2)) < 0.003:
            return None
        P, pl = _frames(played)
        M, ml = _frames(mic)
        if len(M) < _WINDOW or not (ml >= _MIN_LEVEL).any():
            return None if not (ml >= _MIN_LEVEL).any() else False
        # Speaker-to-mic delay: line the loudness envelopes up. Played frame
        # (j + _MAX_LAG - d) is what was playing d frames before mic frame j.
        lm, lp = np.log(ml + 1e-4), np.log(pl + 1e-4)

        def fit(d: int) -> float:
            seg = lp[_MAX_LAG - d:_MAX_LAG - d + len(lm)]
            if len(seg) < len(lm) or seg.std() < 1e-6 or lm.std() < 1e-6:
                return -1.0
            return float(np.corrcoef(seg, lm)[0, 1])
        delay = max(range(-8, _MAX_LAG), key=fit)
        res = []
        for j in range(len(M)):
            if ml[j] < _MIN_LEVEL:
                continue
            tot = float(M[j].sum())
            best = 1.0
            for i in (j + _MAX_LAG - delay - 1, j + _MAX_LAG - delay, j + _MAX_LAG - delay + 1):
                if 0 <= i < len(P):
                    ref = P[i]
                    dd = float(ref @ ref)
                    if dd > 1e-12 and tot > 1e-12:
                        a = max(0.0, float(M[j] @ ref) / dd)
                        best = min(best, float(np.maximum(M[j] - a * ref, 0).sum()) / tot)
            res.append(best)
        if len(res) < _WINDOW:
            return False
        peak = max(float(np.median(res[k:k + _WINDOW])) for k in range(len(res) - _WINDOW + 1))
        # Three answers, not two. Echo-only peaked at 0.17-0.33 in testing; a
        # voice at 0.33-1.0 - but as low as 0.25 when the speaker is louder
        # than the person. So: clearly echo, clearly a voice, or "can't say",
        # which leaves the decision to the word-based check as before.
        if peak < self.threshold:
            # Learn only from what is clearly echo, and from its typical top
            # rather than its maximum: learning from everything let unsure
            # voices push the line up until real voices fell under it.
            self._peaks.append(peak)
            if len(self._peaks) >= 8:
                self.threshold = min(0.34, max(0.26, float(np.percentile(self._peaks, 90)) * 1.05))
            return False
        if peak > self.threshold + 0.06:
            return True
        return None


guard = Echo()
