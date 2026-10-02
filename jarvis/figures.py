"""Finding a real circuit drawing when Athena's own library has none.

Order, best first:
  1. Wikimedia Commons - free to reuse, mostly clean textbook SVGs, and the
     file page says who drew it and under which licence.
  2. A web image search (DuckDuckGo; Google's image API is paid only) - only
     when Commons has nothing textbook-clean.
Every candidate is shown to a Gemini model, which can see, and scored as an
exam-answer diagram. A text model picking by filename would happily embed a
master-slave JK for a NAND JK, a breadboard photo or a watermarked scan; and
on 2 Oct 2026 the first "correct" bridge rectifier found was an explainer
drawing with a battery, a lamp and current arrows - right, but not what goes
in an answer. So the best score wins, and below GOOD nothing goes in.

Found drawings are cached (data/figures/), misses for a day, so asking twice
costs nothing.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
import urllib.parse
import urllib.request

from .config import DATA

CACHE = DATA / "figures"
_UA = {"User-Agent": "Athena/1.0 (personal study assistant; local use)"}
_MAX_BYTES = 4 * 2**20
MISS_TTL = 86400
GOOD, PERFECT = 7, 9

_ASK = ("A student needs the standard textbook circuit diagram of: {what}, as they would draw it "
        "in an exam answer.\n"
        "Score this image 0-10 for that:\n"
        "0 = a different circuit or variant, a photo, a block diagram when a circuit was asked for, "
        "or wrong connections; 3 = right circuit but messy, cropped, mostly text, or under a "
        "watermark or logo; 6 = right and clear but drawn unusually (extra lamps or batteries, "
        "current-flow arrows, two cases in one picture); 9-10 = the clean standard schematic a "
        "textbook prints, every connection right.\n"
        "Check the connections against what that circuit must be. "
        "Reply exactly: SCORE: <n> - <one short reason>.")
_SCORE = re.compile(r"SCORE:\s*(\d+)")


def _get(url: str, timeout: int = 12) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError("too large")
    return data


def _commons(query: str, n: int = 6) -> list[dict]:
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"{query} filetype:drawing|bitmap", "gsrlimit": n,
        "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1000})
    try:
        pages = json.loads(_get("https://commons.wikimedia.org/w/api.php?" + q)).get("query", {}).get("pages", {})
    except Exception:
        return []
    out = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = re.sub(r"<[^>]+>", "", meta.get("LicenseShortName", {}).get("value", "")).strip()
        who = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()[:60]
        if info.get("thumburl"):
            out.append({"url": info["thumburl"], "page": info.get("descriptionurl", ""),
                        "credit": f"Wikimedia Commons · {p.get('title', '').removeprefix('File:')}"
                                  + (f" · {who}" if who else "") + (f" · {lic}" if lic else "")})
    return out


def _web(query: str, n: int = 8) -> list[dict]:
    from ddgs import DDGS
    hits = []
    for wait in (0, 3, 8):          # image search refuses for a moment after a burst
        time.sleep(wait)
        try:
            hits = DDGS().images(query, max_results=n)
        except Exception:
            hits = []
        if hits:
            break
    return [{"url": h["image"], "page": h.get("url", ""),
             "credit": "Web image · " + urllib.parse.urlparse(h.get("url") or h["image"]).netloc}
            for h in hits if h.get("image", "").startswith("http")]


def _as_png(data: bytes) -> bytes | None:
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None
    if min(im.size) < 120:
        return None                         # an icon, not a diagram
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    im = im.convert("RGB")
    im.thumbnail((1400, 1400))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _look(png: bytes, what: str) -> str:
    """A verdict from the first model that can see and is not out of quota:
    Gemini, then Gemma on OpenRouter, then qwen3-vl on this PC. Gemini's free
    tier ran out on 2 Oct 2026 and every check failed, so one provider is not
    enough. Raises if none of them answered."""
    from . import cloud
    from .config import cfg
    from .tools import study
    prompt = _ASK.format(what=what)
    tried = []
    for m in dict.fromkeys([study.current_model() if study.current_model().startswith("gemini:") else "",
                            "gemini:gemini-3.5-flash", "openrouter:google/gemma-4-31b-it:free"]):
        if m and cloud.get_key(cloud.split(m)[0]):
            try:
                return cloud.look(m, png, "image/png", prompt)
            except Exception as e:
                tried.append(f"{m}: {str(e)[:40]}")
    try:
        import ollama
        r = ollama.Client(host=cfg.get("llm.host")).chat(
            model=cfg.get("vision.model", "qwen3-vl:8b-instruct"),
            messages=[{"role": "user", "content": prompt, "images": [png]}], options={"temperature": 0})
        return r["message"]["content"]
    except Exception as e:
        tried.append(f"local: {str(e)[:40]}")
    raise RuntimeError("; ".join(tried))


def find(what: str, note=None) -> dict | None:
    """{"uri": data URI, "credit": str, "page": url, "why": str} for the best
    checked drawing, or None."""
    what = re.sub(r"[_]+", " ", what).strip()      # "jk_to_d_converter" is not a search term
    key = hashlib.sha1(what.lower().strip().encode()).hexdigest()[:16]
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{key}.json"
    if cached.exists():
        c = json.loads(cached.read_text(encoding="utf-8"))
        if c.get("uri") or time.time() - c.get("t", 0) < MISS_TTL:
            return c if c.get("uri") else None
    found, best, checked = None, -1, 0
    for source in (lambda: _commons(f"{what} circuit"), lambda: _web(f"{what} circuit diagram schematic")):
        for cand in source()[:6]:
            try:
                png = _as_png(_get(cand["url"]))
                if not png:
                    continue
                verdict = _look(png, what).strip()
                score = int(_SCORE.search(verdict).group(1))
                checked += 1
            except Exception:
                continue
            if note:
                note(f"diagram check: {cand['credit'][:50]} -> {verdict[:70]}")
            if score >= GOOD and score > best:
                best, found = score, {"uri": "data:image/png;base64," + base64.b64encode(png).decode(),
                                      "credit": cand["credit"], "page": cand["page"], "why": verdict[:200]}
            if best >= PERFECT:
                break
        if best >= PERFECT:
            break       # the web is searched only when Commons has nothing textbook-clean
    if found or checked:        # a miss only counts if something was actually looked at
        cached.write_text(json.dumps(found or {"t": time.time()}), encoding="utf-8")
    return found


if __name__ == "__main__":
    import sys
    r = find(" ".join(sys.argv[1:]) or "full wave bridge rectifier", note=print)
    print("FOUND" if r else "nothing", r and r["credit"], r and r["why"])
