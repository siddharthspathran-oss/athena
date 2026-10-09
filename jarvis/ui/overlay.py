"""A soft glowing orb that appears only while Athena is engaged.

Frames are rendered with numpy - gradients, glow, an organic wobble - and
shown through Tk's colour-key transparency. Everything that fades to pure
black becomes invisible, so the glow blends into a dark desktop rather than
sitting in a box.

(True per-pixel alpha via UpdateLayeredWindow would be nicer still, but Tk
manages its own layered-window state and the two APIs cannot coexist: it
fails with ERROR_INVALID_PARAMETER whatever order they are applied in.)

Every frame is rendered once at startup and cached, so animating costs nothing
but a memory copy. That is what keeps it smooth while Whisper and a 7B model
are both working.

States:
    hidden     nothing on screen - the resting state
    listening  swells and brightens with your voice
    thinking   slow churn while the model works
    speaking   brighter, pulsing while it replies
"""
from __future__ import annotations

import ctypes
import math
import threading
import time
import tkinter as tk

import numpy as np

# --- palette: deep blue core, cyan midtone, near-white glassy shell ---
CORE = (14, 40, 210)
MID = (40, 175, 245)
SHELL = (232, 248, 255)

FRAMES = 40          # frames in one animation cycle
LEVEL_STEPS = 4      # how many voice-loudness variants of the listening orb


# ======================================================================
#  frame rendering
# ======================================================================

def _smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _render(size: int, phase: float, state: str, level: float) -> np.ndarray:
    """One RGBA frame as a uint8 array of shape (size, size, 4)."""
    n = size
    half = n / 2.0
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    dx = (xx - half) / half
    dy = (yy - half) / half

    r = np.sqrt(dx * dx + dy * dy)
    th = np.arctan2(dy, dx)

    # Organic wobble so it breathes like a liquid drop rather than a circle.
    if state == "listening":
        amp, speed, base = 0.050 + 0.030 * level, 1.0, 0.56 + 0.20 * level
    elif state == "speaking":
        amp, speed, base = 0.060, 1.5, 0.70
    else:  # thinking
        amp, speed, base = 0.038, 0.6, 0.60

    wob = (1.0
           + amp * np.sin(3 * th + phase * speed)
           + amp * 0.7 * np.sin(5 * th - 1.7 * phase * speed)
           + amp * 0.5 * np.sin(2 * th + 0.8 * phase * speed))
    radius = base * wob

    # Soft rim, a few pixels wide, is what removes the jagged edge.
    body = _smoothstep((radius - r) / 0.055)

    # The dense core sits slightly off-centre, which is what gives the
    # reference image its sense of depth.
    ox = 0.06 * math.cos(phase * 0.7)
    oy = 0.10 + 0.05 * math.sin(phase * 0.9)
    # Normalise against the smooth nominal radius, never the wobbled one: the
    # wobble depends on the angle, which is undefined at the exact centre, so
    # dividing by it there produces a radial streak through the core.
    rc = np.sqrt((dx - ox) ** 2 + (dy - oy) ** 2) / base

    # shell -> mid -> core, by distance from that offset centre
    t_mid = _smoothstep((0.95 - rc) / 0.55)
    t_core = _smoothstep((0.52 - rc) / 0.42)
    if state == "speaking":
        t_core = np.clip(t_core * 1.15, 0, 1)
    elif state == "listening":
        t_core = np.clip(t_core * (0.85 + 0.35 * level), 0, 1)

    rgb = np.empty((n, n, 3), dtype=np.float32)
    for i in range(3):
        c = SHELL[i] + (MID[i] - SHELL[i]) * t_mid
        c = c + (CORE[i] - c) * t_core
        rgb[..., i] = c

    # Specular highlight near the top-left, like light through glass.
    spec = _smoothstep((0.34 - np.sqrt((dx + 0.26) ** 2 + (dy + 0.30) ** 2)) / 0.34)
    rgb += (255.0 - rgb) * (spec * 0.55)[..., None]

    # Bright inner rim, brightest where the shell meets the outside.
    rim = _smoothstep((r - radius * 0.80) / 0.22) * body
    rgb += (255.0 - rgb) * (rim * 0.45)[..., None]

    alpha = body * 0.97

    # Outer bloom so it sits in the desktop instead of on top of it.
    glow_w = 0.30 if state != "speaking" else 0.40
    glow = np.exp(-np.square(np.clip(r - radius, 0, None) / glow_w)) * (1.0 - body)
    gstr = 0.34 if state != "speaking" else 0.46
    alpha = np.clip(alpha + glow * gstr, 0, 1)
    glow_only = (glow * (1.0 - body))[..., None]
    rgb = rgb * (1 - glow_only) + np.array(MID, dtype=np.float32) * glow_only

    # Force the glow to reach zero before the frame edge. Otherwise the bloom
    # is still faintly lit at the border, and since only exact black is keyed
    # out that leaves a dark square around the orb.
    alpha *= _smoothstep((1.0 - r) / 0.22)

    out = np.empty((n, n, 4), dtype=np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
    return out


def _flatten_to_black(rgba: np.ndarray) -> "Image.Image":
    """Composite onto black, which is the colour keyed out as transparent.

    Anything fully transparent lands on exact black and disappears; the glow
    fades through near-black and blends into a dark desktop.
    """
    from PIL import Image

    a = (rgba[..., 3].astype(np.float32) / 255.0)[..., None]
    rgb = (rgba[..., :3].astype(np.float32) * a).astype(np.uint8)
    # Snap almost-black to exact black so it is keyed out rather than showing
    # as a faint rectangle. Below this level nothing is visible anyway.
    rgb[rgb.max(axis=2) <= 10] = 0
    return Image.fromarray(rgb, "RGB")



# ======================================================================
#  the orb window
# ======================================================================

CHROMA = "#000000"   # keyed out; the render fades to exactly this


class Orb:
    def __init__(self, size: int = 260, position: str = "center",
                 hide_when_idle: bool = True):
        self.size = size
        self.position = position
        self.hide_when_idle = hide_when_idle

        self.state = "hidden"
        self.level = 0.0
        self._i = 0
        self._alive = True
        self._shown = False
        self._photos: dict[str, list] = {}
        self._pending: list[tuple[str, int, object]] = []
        self._ready = False

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=CHROMA)
        self._x, self._y = self._origin()
        self.root.geometry(f"{size}x{size}+{self._x}+{self._y}")

        self.canvas = tk.Canvas(self.root, width=size, height=size, bg=CHROMA,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self._item = self.canvas.create_image(0, 0, anchor="nw")

        # Click-through first, then hand transparency to Tk. Doing it in this
        # order matters: changing the extended style afterwards wipes the
        # colour key Tk installs.
        self._make_click_through()
        try:
            self.root.attributes("-transparentcolor", CHROMA)
        except tk.TclError:
            pass

        self.root.withdraw()
        threading.Thread(target=self._prerender, daemon=True).start()
        self.root.after(16, self._tick)

    # ---------- window ----------

    def _origin(self) -> tuple[int, int]:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        s = self.size
        if self.position == "bottom":
            return (sw - s) // 2, sh - s - 110
        if self.position == "top-right":
            return sw - s - 40, 60
        if self.position == "top-left":
            return 40, 60
        if self.position == "bottom-right":
            return sw - s - 40, sh - s - 110
        if self.position == "bottom-left":
            return 40, sh - s - 110
        return (sw - s) // 2, (sh - s) // 2

    def _make_click_through(self) -> None:
        """Pass clicks through, never take focus, stay out of alt-tab."""
        try:
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            u = ctypes.windll.user32
            hwnd = u.GetParent(self.canvas.winfo_id()) or self.root.winfo_id()
            style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE,
                             style | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
                             | WS_EX_TOOLWINDOW)
        except Exception:
            pass

    # ---------- frames ----------

    def _prerender(self) -> None:
        """Render every frame once, off the UI thread.

        PhotoImage objects can only be built on the Tk thread, so the numpy
        work happens here and the conversion is handed over in small batches.
        """
        t0 = time.time()
        jobs: list[tuple[str, int, object]] = []
        # The particle face from the HUD, pre-rendered by orb_frames.py, when
        # its frames are there; otherwise the original numpy orb below.
        try:
            jobs = self._load_face_frames()
        except Exception as e:
            print(f"[orb: particle frames unusable ({e}) - using the classic orb]")
            jobs = []
        if jobs:
            self._pending = jobs
            self._render_secs = time.time() - t0
            return
        specs = [("thinking", 0.0), ("speaking", 0.0)]
        specs += [(f"listening{b}", b / (LEVEL_STEPS - 1)) for b in range(LEVEL_STEPS)]
        for key, lvl in specs:
            state = "listening" if key.startswith("listening") else key
            for i in range(FRAMES):
                img = _flatten_to_black(
                    _render(self.size, 2 * math.pi * i / FRAMES, state, lvl))
                jobs.append((key, i, img))
        self._pending = jobs
        self._render_secs = time.time() - t0

    def _load_face_frames(self) -> list[tuple[str, int, object]]:
        """data/orb_frames/<key>/NNN.png -> jobs, resized to this orb's size.

        Keys: thinking, speaking, listening0..N (quiet to loud). Sets FRAMES
        and the listening steps from what is on disk.
        """
        global FRAMES, LEVEL_STEPS
        from PIL import Image
        from ..config import DATA

        root = DATA / "orb_frames"
        keys = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
        listening = [k for k in keys if k.startswith("listening")]
        if not ({"thinking", "speaking"} <= set(keys) and listening):
            return []
        n = min(len(list((root / k).glob("*.png"))) for k in keys)
        if n < 8:
            return []
        jobs = []
        for key in keys:
            for i in range(n):
                img = Image.open(root / key / f"{i:03d}.png").convert("RGB")
                if img.size != (self.size, self.size):
                    img = img.resize((self.size, self.size), Image.LANCZOS)
                jobs.append((key, i, img))
        FRAMES, LEVEL_STEPS = n, len(listening)
        return jobs

    def _drain_pending(self) -> None:
        if self._ready or not self._pending:
            return
        from PIL import ImageTk

        # A few per tick keeps the UI responsive while these are built.
        for _ in range(24):
            if not self._pending:
                break
            key, idx, img = self._pending.pop(0)
            self._photos.setdefault(key, [None] * FRAMES)[idx] = ImageTk.PhotoImage(img)
        if not self._pending:
            self._ready = True
            n = sum(len(v) for v in self._photos.values())
            print(f"[orb: {n} frames ready in {self._render_secs:.1f}s]")

    # ---------- called from the voice thread ----------

    def set_state(self, state: str) -> None:
        self.state = "hidden" if state in ("idle", "off") else state

    def set_level(self, rms: float) -> None:
        target = min(1.0, rms / 0.085)
        self.level += (target - self.level) * 0.4

    def stop(self) -> None:
        self._alive = False
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()

    # ---------- animation ----------

    def _key(self) -> str | None:
        if self.state == "listening":
            b = int(round(self.level * (LEVEL_STEPS - 1)))
            return f"listening{max(0, min(LEVEL_STEPS - 1, b))}"
        if self.state in ("thinking", "speaking"):
            return self.state
        return None

    def _tick(self) -> None:
        if not self._alive:
            return
        self._drain_pending()

        key = self._key()
        if key is not None and self._ready:
            if not self._shown:
                self.root.deiconify()
                self.root.attributes("-topmost", True)
                self._shown = True
            self._i = (self._i + 1) % FRAMES
            photo = self._photos.get(key, [None])[self._i]
            if photo is not None:
                self.canvas.itemconfig(self._item, image=photo)
        elif self._shown:
            self.root.withdraw()
            self._shown = False

        # ~30 fps while on screen. Hidden - which is most of the day - there
        # is nothing to draw, so check ten times a second instead of thirty
        # once the frames are built.
        busy = self._shown or not self._ready
        self.root.after(33 if busy else 100, self._tick)
