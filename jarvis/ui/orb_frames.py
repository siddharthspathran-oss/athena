"""Pre-render the desktop overlay's frames from the HUD's particle face (face.js).

    python -m jarvis.ui.orb_frames          # writes data/orb_frames/<state>/NNN.png

The overlay is a Tk window that can only show still images, so the face is
drawn frame by frame in headless Chrome, once, and saved. Each state is a loop:
the last FADE frames are cross-faded into the first ones so it never jumps.
Frames are on black, which the overlay keys out as transparent.
"""
from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

from ..config import DATA

OUT = DATA / "orb_frames"
FACE = Path(__file__).parent / "face.js"
FPS, FRAMES, FADE = 30, 72, 18
# key -> (face state, voice level). Listening has two loudness variants.
STATES = {"listening0": ("listening", 0.08), "listening1": ("listening", 0.6),
          "thinking": ("thinking", 0.0), "speaking": ("speaking", 0.0)}


def build(size: int = 260) -> int:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    page = (f"<!doctype html><html><body style='margin:0;background:#000'>"
            f"<canvas id='c' style='width:{size}px;height:{size}px;display:block'></canvas>"
            f"<script>{FACE.read_text(encoding='utf-8')}</script>"
            "<script>window.face=new AthenaFace(document.getElementById('c'),{manual:true});</script>"
            "</body></html>")
    tmp = OUT.with_name("orb_frames.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        pg = b.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
        pg.set_content(page)
        pg.wait_for_function("window.face")
        for key, (state, level) in STATES.items():
            pg.evaluate(f"face.setState('{state}'); face.setLevel({level})")
            shots = []
            for k in range(FRAMES + FADE):
                pg.evaluate(f"face.renderAt({k / FPS})")
                shots.append(Image.open(io.BytesIO(pg.screenshot())).convert("RGB"))
            d = tmp / key
            d.mkdir()
            for j in range(FRAMES):
                img = shots[j]
                if j < FADE:     # continue the end of the loop into its start
                    img = Image.blend(shots[FRAMES + j], shots[j], j / FADE)
                # near-black snaps to black, so it is keyed out, not a faint box
                img = img.point(lambda v: 0 if v <= 6 else v)
                img.save(d / f"{j:03d}.png", optimize=True)
            print(f"  {key}: {FRAMES} frames")
        b.close()
    shutil.rmtree(OUT, ignore_errors=True)
    tmp.rename(OUT)
    return FRAMES * len(STATES)


if __name__ == "__main__":
    n = build(int(sys.argv[1]) if len(sys.argv) > 1 else 260)
    print(f"{n} frames written to {OUT}")
