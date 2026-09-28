"""Looking at pictures: the webcam, and images you attach.

The everyday model here is text-only, so an image needs a different one:
qwen3-vl:8b-instruct. What it replaced, measured on this 8GB RTX 4060:
moondream (1.7GB) returned an empty string for any real question and on a
webcam frame with a face in it captioned "urn of water on a table";
gemma4:26b (18GB) answered but ran 91% on the CPU, two to three minutes per
picture. qwen3-vl fits entirely on the GPU at a 2048 context and answers a
webcam question in 1.6-1.8s warm. The "instruct" tag has no thinking mode,
so it cannot burn its token budget reasoning and return nothing - the
failure gemma4 had until thinking was switched off.

The webcam is captured with ffmpeg rather than OpenCV: ffmpeg is already
installed, OpenCV is not, and one frame from DirectShow needs no more.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from .config import DATA, cfg

CAPTURES = DATA / "captures"


def model() -> str:
    return str(cfg.get("vision.model", "qwen3-vl:8b-instruct"))


def available() -> tuple[bool, str]:
    """Is a vision model installed? Returns (ok, message)."""
    try:
        import ollama

        names = [m.model for m in ollama.list().models]
    except Exception as e:
        return False, f"Cannot reach Ollama: {e}"
    want = model()
    if want in names or any(n.split(":")[0] == want.split(":")[0] for n in names):
        return True, want
    return False, (f"No vision model. Install one with:  ollama pull {want}")


_THINKS: dict[str, bool] = {}


def _thinks(name: str) -> bool:
    """Does this model have a thinking mode? Asked once, then remembered.

    It matters because a thinking model spends its token budget reasoning and
    returns an empty description unless thinking is turned off - but passing
    the flag to a model that has no such mode is asking for trouble, and
    moondream does not.
    """
    if name in _THINKS:
        return _THINKS[name]
    try:
        import ollama

        caps = getattr(ollama.show(name), "capabilities", []) or []
        _THINKS[name] = "thinking" in [str(c).lower() for c in caps]
    except Exception:
        _THINKS[name] = False
    return _THINKS[name]


def describe(image: Path, question: str = "") -> str:
    """Ask the vision model about an image.

    A blank reply falls through to vision.fallback_model if one is set. It
    existed for moondream, which answered questions with an empty string;
    qwen3-vl answers them, so the fallback is normally empty.
    """
    ok, msg = available()
    if not ok:
        return f"ERROR: {msg}"
    if not image.is_file():
        return f"ERROR: no such image: {image}"

    _hold()
    try:
        answer = _ask(image, question, model())
        spare = str(cfg.get("vision.fallback_model", "") or "").strip()
        if not answer and spare and spare != model():
            answer = _ask(image, question, spare)
    finally:
        _release()
    return answer or "The vision model had nothing to say about that image."


# --- Handing the card back to the everyday model -------------------------
#
# qwen2.5:7b (5.1GB) and qwen3-vl (5.5GB) do not fit in 8GB together, so a
# picture question evicts the everyday model and the next text question used
# to pay ~6.5s for it to reload. Instead, vision.reload_after seconds after
# the last picture question, this unloads the vision model and loads the
# everyday one in the background, so that wait lands while nobody is asking.
# A new picture question cancels the pending swap, which keeps a quick
# follow-up about the same photo at 1.1-1.3s instead of 11s.
#
# One timer per process, at module level, so it covers every caller: the web
# server's /look and attachments, and the Tk GUI. Two processes each keep
# their own; the worst that does is unload the vision model under the other
# one's question, which then just reloads it.

_lock = threading.Lock()
_timer: threading.Timer | None = None
_busy = 0          # picture questions in flight in this process
_gen = 0           # bumped by every question, so a stale timer can tell


def _hold() -> None:
    """A picture question is starting: call off any pending swap."""
    global _timer, _busy, _gen
    with _lock:
        _busy += 1
        _gen += 1
        if _timer is not None:
            _timer.cancel()
            _timer = None


def _release() -> None:
    """A picture question finished: (re)arm the swap back."""
    global _timer, _busy
    try:
        delay = float(cfg.get("vision.reload_after", 45) or 0)
    except (TypeError, ValueError):
        delay = 45.0
    with _lock:
        _busy = max(0, _busy - 1)
        if _busy or delay <= 0:
            return
        if _timer is not None:
            _timer.cancel()
        t = threading.Timer(delay, _swap_back, args=(_gen,))
        t.daemon = True     # never hold a process open on exit
        t.name = "vision-swap-back"
        _timer = t
        t.start()


def _swap_back(gen: int) -> None:
    """Unload the vision model, then warm the everyday one. Never raises."""
    global _timer
    with _lock:
        if gen != _gen or _busy:
            return      # a newer question came in; its own timer will do this
        _timer = None
    try:
        import ollama

        name = model()
        everyday = str(cfg.get("llm.model", "qwen2.5:7b"))
        try:
            loaded = {m.model for m in ollama.ps().models}
        except Exception:
            loaded = {name}
        if name in loaded:
            # keep_alive=0 with no prompt unloads at once rather than waiting
            # out vision.keep_alive, so the everyday model has the room.
            ollama.generate(model=name, keep_alive=0)
        if everyday in loaded:
            return
        from . import gaming
        if gaming.on():
            return      # gaming mode: leave the card empty
        with _lock:
            if gen != _gen or _busy:
                return  # a picture question arrived while unloading
        # Same host, keep_alive and num_ctx as llm.LLM.chat. num_ctx matters
        # most: Ollama keys a loaded model on its context size, so warming it
        # at the default 4096 would just mean a second reload at 8192 on the
        # real request.
        client = ollama.Client(host=cfg.get("llm.host",
                                            "http://127.0.0.1:11434"))
        client.chat(
            model=everyday,
            messages=[{"role": "user", "content": "hi"}],
            keep_alive=cfg.get("llm.keep_alive", "30m"),
            options={"num_predict": 1,
                     "num_ctx": cfg.get("llm.context_window", 8192)},
        )
    except Exception:
        # Best effort: if it fails, the next text question loads the model
        # itself, which is exactly what happened before this existed.
        pass


def _shrink(image: Path) -> bytes | str:
    """The image as JPEG bytes, scaled down to at most vision.max_pixels.

    qwen3-vl spends tokens in proportion to pixels: a 1280x720 webcam frame
    is 1091 prompt tokens, a 4000x3000 phone photo was 4031 and Ollama
    refused it outright against the 2048 context that keeps the model fully
    on the GPU. Scaling to the webcam's area keeps every image under ~1100.
    """
    limit = int(cfg.get("vision.max_pixels", 1280 * 720))
    try:
        import io

        from PIL import Image, ImageOps

        with Image.open(image) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            w, h = im.size
            if w * h > limit:
                k = (limit / (w * h)) ** 0.5
                im = im.resize((max(1, int(w * k)), max(1, int(h * k))),
                               Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
            return buf.getvalue()
    except Exception:
        # Not something PIL can read: let Ollama try the original file.
        return str(image)


_SYSTEM = ("You are looking at a photo from the user's webcam, or an image "
           "they attached. Answer the user's question about what is in the "
           "photo. The question is never about you: \"is anyone there?\" "
           "means is there a person in the photo.")


def _ask(image: Path, question: str, name: str) -> str:
    """One attempt with one model. Empty string means it said nothing."""
    import ollama

    q = question.strip() or "What do you see?"
    # Asked a bare "is anyone there?", the old fallback replied "Yes, I am
    # here! I am an AI assistant" - it took the question as addressed to
    # itself. Framing every question as one about the photo fixes that.
    prompt = (f"Question about this photo: {q}\n"
              "Answer from what is visible in the photo, in one to three "
              "sentences.")
    extra = {}
    if _thinks(name):
        # A thinking model asked to describe a photo spends the whole budget
        # reasoning and returns an empty string.
        extra["think"] = False
    try:
        reply = ollama.chat(
            model=name,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt, "images": [_shrink(image)]},
            ],
            options={"num_predict": int(cfg.get("vision.max_tokens", 300)),
                     "num_ctx": int(cfg.get("vision.num_ctx", 2048))},
            keep_alive=cfg.get("vision.keep_alive", "2m"),
            **extra,
        )
    except Exception as e:
        return f"ERROR: {name} failed: {e}"
    # Empty is meaningful to the caller - it is the signal to try the other
    # model - so it is returned as-is rather than dressed up as an answer.
    return (reply.message.content or "").strip()


def camera_name() -> str:
    return str(cfg.get("vision.camera", "Integrated Camera"))


def cameras() -> list[str]:
    """Names of the DirectShow video devices ffmpeg can see."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow",
             "-i", "dummy"],
            capture_output=True, text=True, timeout=25, check=False,
        )
    except Exception:
        return []
    found = []
    for line in (r.stderr or "").splitlines():
        if "(video)" in line and '"' in line:
            found.append(line.split('"')[1])
    return found


def capture(dest: Path | None = None, timeout: float = 30.0) -> Path | str:
    """Take one frame from the webcam. Returns the path, or an error string."""
    CAPTURES.mkdir(parents=True, exist_ok=True)
    dest = dest or CAPTURES / f"cam_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    size = str(cfg.get("vision.capture_size", "1280x720"))

    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "dshow", "-video_size", size,
             "-i", f"video={camera_name()}",
             "-frames:v", "1", "-q:v", "3", "-y", str(dest)],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError:
        return "ERROR: ffmpeg is not installed, so the camera cannot be used."
    except subprocess.TimeoutExpired:
        return "ERROR: the camera did not respond in time."

    if not dest.is_file() or dest.stat().st_size == 0:
        detail = (r.stderr or "").strip().splitlines()
        names = cameras()
        hint = f" Cameras found: {', '.join(names)}." if names else ""
        return (f"ERROR: could not capture from '{camera_name()}'."
                f"{hint} {detail[-1] if detail else ''}".strip())
    return dest
