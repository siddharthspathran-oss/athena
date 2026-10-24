"""An animated page -> a real MP4 (what a "make me a video" mission delivers).

Workers can't install anything (no MoviePy, no ffmpeg install), and they're
very good at animation in HTML/CSS/canvas. So the worker builds the motion
graphic as a page, and Athena films it here: headless Chrome (Playwright,
like upes.py), one exact frame at a time through the page's
window.renderFrame(t), encoded with PyAV's libx264 - smooth 30 fps, no dropped
frames, whatever the PC is doing. A page without renderFrame is recorded in
real time instead (less smooth, still a video).

The page contract (VIDEO_RULES) is handed to the worker by missions.py.
"""
from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path

DEFAULTS = {"width": 1920, "height": 1080, "seconds": 15, "fps": 30}

VIDEO_RULES = (
    "This becomes a VIDEO: Athena films index.html frame by frame into an MP4, so build it as an animation page "
    "that follows these rules exactly. (1) Set window.ATHENA_VIDEO = {width, height, seconds, fps: 30} - 1920x1080 "
    "landscape unless a vertical/phone/reel video was asked for (then 1080x1920). (2) Draw everything inside a fixed "
    "stage of exactly that size at the top-left of the page, body margin 0, no scrollbars. (3) ALL motion must come "
    "from one function window.renderFrame(t) (t = seconds from 0 to the duration) that sets the whole scene for time "
    "t - CSS transforms/opacity set from t, canvas or SVG drawn for t. No CSS @keyframes, no CSS transitions, no "
    "setTimeout/setInterval timing, no Math.random without a fixed seed: the same t must always give the same "
    "frame. (4) When window.__athenaRecording is not true, the page plays itself in a loop with requestAnimationFrame "
    "calling renderFrame, so it can be previewed in a browser. (5) Plain HTML/CSS/JS, no libraries; Google Fonts are "
    "fine. Make it look professional: real easing, layered motion, typography that's legible at 1080p, a clear "
    "beginning, middle and end within the duration. Don't try to make the MP4 yourself.")


def _encoder(path: Path, w: int, h: int, fps: int):
    import av
    # faststart: the index (moov) at the front, so phones and embedded players can
    # start playing straight away - at the end, the HUD's player didn't open it (17 Oct 2026).
    out = av.open(str(path), "w", options={"movflags": "faststart"})
    for name, opts in (("libx264", {"crf": "18", "preset": "medium"}), ("h264_mf", {}), ("mpeg4", {"qscale": "2"})):
        try:
            st = out.add_stream(name, rate=fps)
            st.width, st.height, st.pix_fmt = w, h, "yuv420p"
            st.options = opts
            return out, st
        except Exception:
            continue
    raise RuntimeError("no video encoder available")


def render(folder: Path, out_name: str = "video.mp4", seconds: float | None = None, progress=None) -> dict:
    """index.html in folder -> folder/out_name. Returns what was made."""
    import av
    import numpy as np
    from PIL import Image
    from playwright.sync_api import sync_playwright

    page = folder / "index.html"
    if not page.exists():
        raise RuntimeError("there's no index.html to film")
    out = folder / out_name
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
            pg = ctx.new_page()
            pg.add_init_script("window.__athenaRecording = true;")
            pg.goto(page.as_uri(), wait_until="load", timeout=45000)
            pg.evaluate("document.fonts ? document.fonts.ready.then(() => true) : true")
            pg.wait_for_timeout(800)
            meta = {**DEFAULTS, **(pg.evaluate("window.ATHENA_VIDEO || null") or {})}
            if seconds:
                meta["seconds"] = seconds
            w, h = int(meta["width"]) // 2 * 2, int(meta["height"]) // 2 * 2
            fps, secs = max(12, min(60, int(meta["fps"]))), max(1.0, min(120.0, float(meta["seconds"])))
            exact = pg.evaluate("typeof window.renderFrame === 'function'")
            if exact:
                pg.set_viewport_size({"width": w, "height": h})
                pg.wait_for_timeout(300)
                container, st = _encoder(out, w, h, fps)
                n = int(round(secs * fps))
                for i in range(n):
                    pg.evaluate(f"window.renderFrame({i / fps:.5f})")
                    png = pg.screenshot(type="png", clip={"x": 0, "y": 0, "width": w, "height": h})
                    frame = av.VideoFrame.from_ndarray(np.asarray(Image.open(io.BytesIO(png)).convert("RGB")),
                                                       format="rgb24")
                    for pkt in st.encode(frame):
                        container.mux(pkt)
                    if progress and i and i % (fps * 3) == 0:
                        progress(f"filming frame {i} of {n}")
                for pkt in st.encode():
                    container.mux(pkt)
                container.close()
            else:                                    # no renderFrame: film it as it plays
                ctx.close()
                tmp = Path(tempfile.mkdtemp(prefix="athena-video-"))
                ctx = browser.new_context(viewport={"width": w, "height": h}, record_video_dir=str(tmp),
                                          record_video_size={"width": w, "height": h})
                pg = ctx.new_page()
                pg.goto(page.as_uri(), wait_until="load", timeout=45000)
                pg.wait_for_timeout(int(secs * 1000) + 600)
                raw = Path(pg.video.path())
                ctx.close()
                src = av.open(str(raw))
                container, st = _encoder(out, w, h, fps)
                for f in src.decode(video=0):
                    for pkt in st.encode(f.reformat(width=w, height=h, format="yuv420p")):
                        container.mux(pkt)
                for pkt in st.encode():
                    container.mux(pkt)
                container.close()
                src.close()
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            browser.close()
    return {"file": out_name, "seconds": secs, "width": w, "height": h, "fps": fps, "exact": exact}


if __name__ == "__main__":
    d = Path(tempfile.mkdtemp(prefix="athena-videotest-"))
    (d / "index.html").write_text(
        "<body style='margin:0'><div id=s style='width:320px;height:180px;background:#111;position:relative'>"
        "<div id=b style='position:absolute;top:70px;width:40px;height:40px;background:#f60'></div></div><script>"
        "window.ATHENA_VIDEO={width:320,height:180,seconds:1,fps:12};"
        "window.renderFrame=t=>{document.getElementById('b').style.left=(t*280)+'px'};renderFrame(0)</script>",
        encoding="utf-8")
    info = render(d)
    import av
    v = av.open(str(d / "video.mp4"))
    frames = sum(1 for _ in v.decode(video=0))
    v.close()
    data = (d / "video.mp4").read_bytes()
    assert data.find(b"moov") < data.find(b"mdat"), "not faststart"
    assert info["exact"] and frames == 12 and (info["width"], info["height"]) == (320, 180), (info, frames)
    shutil.rmtree(d, ignore_errors=True)
    print("videorender: checks pass")
