"""Screen awareness: look at what the user is looking at, and answer about it.

Three things are gathered, only when asked - nothing watches the screen:
  1. which window is in front, and its title;
  2. the text in it, read straight from the app through UI Automation - the
     same route the app-control tools use. Exact where a screenshot is fuzzy:
     an error message, a code line, a form field's value;
  3. a screenshot of that window (or the whole screen when asked).
All of it goes to the local vision model (vision.model, qwen3-vl) in one
prompt. Nothing leaves the PC.

Sized to stay on the GPU: at a 4096 context qwen3-vl was 6.2 GB and ran
11% on the CPU (27.5 s cold); at 3072, 9% (16 s cold, 5.9 s warm). 2048
fits whole - a window screenshot is ~1,100 tokens, so the window text is
capped at ~1,300 characters and the answer at 320 tokens to fit beside it.

The window in front when you ask is usually Athena's own HUD or the popup,
so those are skipped and the window under them is the one looked at.
"""
from __future__ import annotations

import ctypes
import re
import time
from pathlib import Path

from .. import uia, winapi
from ..config import DATA, cfg
from ..registry import tool

TAG = ["system"]
SHOTS = DATA / "screenshots"
_SKIP_TITLES = ("Athena", "Athena wake watch", "Program Manager", "")


def _target_window() -> tuple[int, str]:
    """The window the user means: the one in front, unless that is Athena."""
    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    order = [(h, t) for h, t in winapi.list_windows()]
    for h, t in order:
        if h == fg and t not in _SKIP_TITLES and not t.startswith("Athena -"):
            return h, t
    # Next visible, non-minimised window in z-order below the foreground one.
    h = user32.GetWindow(fg, 2)                       # GW_HWNDNEXT
    titles = dict(order)
    while h:
        t = titles.get(h, "")
        if (t and t not in _SKIP_TITLES and user32.IsWindowVisible(h)
                and not user32.IsIconic(h)):
            return h, t
        h = user32.GetWindow(h, 2)
    return 0, ""


def _window_text(hwnd: int, limit: int = 1300) -> str:
    """Visible text in the window, via UI Automation. Best effort."""
    try:
        uia.wake(hwnd, settle=0.4)
        client, uiac = uia._client()
        root = client.ElementFromHandle(hwnd)
        parts: list[str] = []
        # Documents (a web page, an editor) give their whole text at once.
        docs = root.FindAll(uiac.TreeScope_Descendants,
                            client.CreatePropertyCondition(uiac.UIA_ControlTypePropertyId, 50030))
        for i in range(min(docs.Length, 2)):
            try:
                tp = docs.GetElement(i).GetCurrentPattern(uiac.UIA_TextPatternId)
                if tp:
                    txt = tp.QueryInterface(uiac.IUIAutomationTextPattern).DocumentRange.GetText(limit)
                    if txt.strip():
                        parts.append(txt)
            except Exception:
                continue
        if sum(len(p) for p in parts) < 200:
            # Otherwise the names of on-screen text, buttons and fields.
            seen = set()
            for el in uia.elements(hwnd):
                if not el.on_screen:
                    continue
                n = el.name
                if n and len(n) > 1 and n not in seen:
                    seen.add(n)
                    parts.append(n)
                if sum(len(p) for p in parts) > limit:
                    break
        text = re.sub(r"[ \t]+", " ", "\n".join(parts))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:limit]
    except Exception:
        return ""


def capture(whole: bool = False) -> tuple[Path | None, str, str]:
    """(screenshot path, window title, window text)."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"screen_{time.strftime('%Y%m%d_%H%M%S')}.png"
    hwnd, title = (0, "") if whole else _target_window()
    region = None
    if hwnd:
        l, t, r, b = winapi.window_rect(hwnd)
        if r - l > 50 and b - t > 50 and l > -10000:
            region = (max(l, 0), max(t, 0), r - max(l, 0), b - max(t, 0))
    try:
        winapi.screenshot(path, region)
    except Exception:
        return None, title, ""
    return path, title, _window_text(hwnd) if hwnd else ""


_SYSTEM = ("You are Athena, looking at the user's own computer screen because they asked you to. "
           "Answer their question about what is on it. Use the window text when it is given - it is "
           "exact, the screenshot is not. Be concrete and brief: two to five sentences unless they "
           "asked for detail. If something on screen is an error, say what it means and how to fix it.")


@tool(tags=TAG)
def look_at_screen(question: str = "What am I looking at?", whole_screen: bool = False) -> str:
    """Look at the user's screen and answer about it: what is on it, read or
    summarise it, explain an error, help with what they are doing.

    Use for "what's on my screen", "what am I looking at", "read this",
    "explain this error", "summarise this page", "help me with this".

    question: what the user wants to know about their screen
    whole_screen: true to look at every monitor instead of just the window in front
    """
    import ollama

    shot, title, text = capture(whole_screen)
    if shot is None:
        return "ERROR: couldn't capture the screen."
    from ..vision import _shrink

    prompt = f"Question: {question.strip() or 'What am I looking at?'}\n"
    if title:
        prompt += f"\nWindow in front: {title}\n"
    if text:
        prompt += f"\nText read from that window:\n---\n{text}\n---\n"
    from .. import vision
    vision._hold()                           # keep the swap-back timer off meanwhile
    try:
        reply = ollama.chat(
            model=vision.model(),
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": prompt, "images": [_shrink(shot)]}],
            options={"num_predict": int(cfg.get("screen.max_tokens", 320)),
                     "num_ctx": int(cfg.get("screen.num_ctx", 2048)),
                     "temperature": 0.2},
            keep_alive=cfg.get("vision.keep_alive", "2m"),
        )
    except Exception as e:
        return f"ERROR: the vision model failed ({e})."
    finally:
        vision._release()                    # hand the GPU back to the chat model later
    answer = (reply.message.content or "").strip()
    return answer or "I looked, but couldn't make out anything to say about it."
