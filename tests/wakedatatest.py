"""Wake-word data from real use (jarvis/wakedata.py), on your own recordings.

    python tests/wakedatatest.py

Your takes in data/wake_mine stand in for a real "Athena", the room talk in
data/wake_room for sound without the name. The current detector was trained
on these takes, so the windows kept for a take must score high with it - that
proves they are cut where the trainer and the live detector look.
"""
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import wakedata  # noqa: E402
from jarvis.config import DATA  # noqa: E402

bad = []
wakedata.FOLDER = Path(tempfile.mkdtemp()) / "wake_real"


def read(p):
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


takes = sorted((DATA / "wake_mine").glob("*.wav"))[:6]
for t in takes:
    a = read(t)
    loud = np.flatnonzero(np.abs(a) > 0.1 * np.abs(a).max())
    wakedata.positive(np.concatenate([a, np.zeros(16000, np.float32)]), loud[-1] / 16000)   # a pause after the name
    time.sleep(0.3)                     # one at a time: the queue drops work it can't keep up with
talk = read(DATA / "wake_room" / "talk.wav")
wakedata.negative(talk)
wakedata.negative(talk[::-1].copy())

deadline = time.time() + 60
while time.time() < deadline and (len(list(wakedata.FOLDER.glob("pos-*.npy"))) < len(takes)
                                  or not list(wakedata.FOLDER.glob("neg-*.npy"))):
    time.sleep(0.5)
pos = [np.load(f) for f in sorted(wakedata.FOLDER.glob("pos-*.npy"))]
neg = [np.load(f) for f in wakedata.FOLDER.glob("neg-*.npy")]
if len(pos) != len(takes) or any(p.shape != (3, 16, 96) or p.dtype != np.float16 for p in pos):
    bad.append(f"positives kept: {[p.shape for p in pos]} for {len(takes)} takes")
if not neg or neg[0].shape[1:] != (16, 96):
    bad.append(f"no negatives flushed: {[n.shape for n in neg]}")

import onnxruntime as rt  # noqa: E402
s = rt.InferenceSession(str(DATA / "athena_wake.onnx"), providers=["CPUExecutionProvider"])
score = lambda x: float(s.run(None, {s.get_inputs()[0].name: x[None].astype(np.float32)})[0].ravel()[0])  # noqa: E731
ps = [max(score(w) for w in p) for p in pos]
ns = [score(w) for n in neg for w in n]
print(f"best score per take: {[round(x, 2) for x in ps]}")
print(f"room talk: {len(ns)} windows, {sum(x >= 0.5 for x in ns)} at 0.5 or more")
if np.median(ps) < 0.7:
    bad.append("the kept windows for a take don't look like 'Athena' to the detector - cut in the wrong place")
if np.mean(np.array(ns) >= 0.5) > 0.05:
    bad.append("room talk looks like 'Athena'")
for f in wakedata.FOLDER.glob("*.npy"):
    f.unlink()                          # the test's own temp files
for x in bad:
    print("FAIL", x)
print(f"wakedata: {len(bad)} failure(s)")
sys.exit(1 if bad else 0)
