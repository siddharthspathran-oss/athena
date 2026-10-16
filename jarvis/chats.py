"""Saved conversations, so closing the window does not throw the thread away.

Until now the transcript lived only in memory: every restart began with a
blank slate, and anything worked out in a previous session was gone. The
facts you explicitly asked it to remember survived (memory.json), but the
conversation around them did not.

One file per conversation in data/chats/, newest-first by when it was last
touched. Deliberately files rather than a database - they can be read, backed
up, and deleted with Explorer, and a corrupt one costs a single conversation
rather than the lot.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import DATA
from .store import load_json, save_json

CHATS = DATA / "chats"

# Enough to find a conversation again, not enough to fill the disk. A long
# session of worksheet answers can run to megabytes, and the model only ever
# sees the last few turns anyway.
MAX_STORED = 400


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _path(chat_id: str) -> Path:
    # The id is ours, but a caller could pass anything; never leave the folder.
    return CHATS / f"{Path(chat_id).name}.json"


def title_for(messages: list[dict[str, Any]]) -> str:
    """Name a conversation after the first thing asked in it."""
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            text = re.sub(r"\s+", " ", m["content"]).strip()
            if text:
                return text[:48] + ("…" if len(text) > 48 else "")
    return "New chat"


def save(chat_id: str, messages: list[dict[str, Any]],
         title: str = "", project: str = "") -> None:
    """Write a conversation to disk. Empty ones are not worth keeping."""
    real = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not real:
        return
    CHATS.mkdir(parents=True, exist_ok=True)
    existing = load_json(_path(chat_id), {})
    save_json(_path(chat_id), {
        **existing,
        **({"project": project} if project and not existing.get("project") else {}),
        "id": chat_id,
        "title": title or existing.get("title") or title_for(messages),
        "created": existing.get("created") or time.time(),
        "updated": time.time(),
        "messages": messages[-MAX_STORED:],
    })
    if not existing.get("titled") and any(m.get("role") == "assistant" for m in real):
        threading.Thread(target=_smart_title, args=(chat_id, real[:4]), daemon=True).start()


def _smart_title(chat_id: str, first: list[dict[str, Any]]) -> None:
    """A short ChatGPT-style name from the first exchange ("Fourier series
    derivation"), in the background; the first words stay until it's ready."""
    try:
        from .llm import LLM
        ask = next((m for m in first if m["role"] == "user"), None)
        if ask is None or ask.get("private"):
            return
        reply = next((m for m in first[first.index(ask):] if m["role"] == "assistant"), {"content": ""})
        msg = LLM().chat([{"role": "user", "content":
                           "Name a chat after the user's request below: 2 to 6 words, Title Case, "
                           "no quotes, no full stop. Describe what they asked about, nothing else. "
                           "Reply with the name only.\n\nRequest: " + str(ask["content"])[:400]
                           + "\nAnswer: " + str(reply.get("content", ""))[:300]}],
                         temperature=0.2, max_tokens=20)
        name = re.sub(r"[\"'*#.]+", "", (msg.content or "").strip().splitlines()[0] if msg.content else "").strip()
    except Exception:
        return
    data = load_json(_path(chat_id), {})
    if data and 2 <= len(name) <= 60 and not data.get("renamed"):
        save_json(_path(chat_id), {**data, "title": name, "titled": True})


def rename(chat_id: str, title: str) -> bool:
    data = load_json(_path(chat_id), {})
    title = re.sub(r"\s+", " ", title).strip()[:80]
    if not data or not title:
        return False
    save_json(_path(chat_id), {**data, "title": title, "titled": True, "renamed": True})
    return True


def pin(chat_id: str, on: bool) -> bool:
    data = load_json(_path(chat_id), {})
    if not data:
        return False
    save_json(_path(chat_id), {**data, "pinned": bool(on)})
    return True


def load(chat_id: str) -> list[dict[str, Any]]:
    """The messages of one conversation, or an empty list."""
    data = load_json(_path(chat_id), {})
    messages = data.get("messages")
    return messages if isinstance(messages, list) else []


def listing() -> list[dict[str, Any]]:
    """Every saved conversation, most recently used first."""
    if not CHATS.is_dir():
        return []
    out = []
    for f in CHATS.glob("*.json"):
        data = load_json(f, {})
        if not data.get("id"):
            continue
        out.append({
            "id": data["id"],
            "title": data.get("title") or "New chat",
            "updated": data.get("updated", 0),
            "turns": sum(1 for m in data.get("messages", [])
                         if m.get("role") == "user"),
            "pinned": bool(data.get("pinned")),
            "project": data.get("project", ""),
        })
    out.sort(key=lambda c: c["updated"], reverse=True)
    return out


def meta(chat_id: str) -> dict[str, Any]:
    """Everything about a chat except its messages."""
    data = load_json(_path(chat_id), {})
    data.pop("messages", None)
    return data


def update(chat_id: str, **fields: Any) -> bool:
    data = load_json(_path(chat_id), {})
    if not data:
        return False
    save_json(_path(chat_id), {**data, **fields})
    return True


def search(query: str, limit: int = 40) -> list[dict[str, Any]]:
    """Chats whose title or messages contain the words, with a snippet around
    the first hit - the search box looks inside chats, not just at titles."""
    words = [w for w in query.lower().split() if w]
    if not words or not CHATS.is_dir():
        return []
    out = []
    for c in listing():
        title = c["title"].lower()
        texts = [str(m.get("content") or "") for m in load(c["id"])
                 if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
        body = "\n".join(texts).lower()
        if not all(w in title or w in body for w in words):
            continue
        snippet = ""
        for t in texts:
            i = t.lower().find(words[0])
            if i >= 0:
                snippet = ("…" if i > 40 else "") + re.sub(r"\s+", " ", t[max(0, i - 40):i + 80]).strip() + "…"
                break
        out.append({**c, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def export_md(chat_id: str) -> str:
    """The chat as Markdown, for saving or sharing."""
    data = load_json(_path(chat_id), {})
    lines = [f"# {data.get('title') or 'Chat'}", "",
             f"_Athena · {time.strftime('%d %b %Y, %H:%M', time.localtime(data.get('updated') or time.time()))}_", ""]
    for m in data.get("messages", []):
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
            lines += [f"**{'You' if m['role'] == 'user' else 'Athena'}:**", "", m["content"].strip(), ""]
    return "\n".join(lines)


# ---------- projects: groups of chats with notes Athena always keeps in mind ----------

PROJECTS = DATA / "projects.json"
ACTIVE: dict[str, Any] | None = None     # the open chat's project, set by Memory.build


def projects() -> list[dict[str, Any]]:
    return load_json(PROJECTS, [])


def project(pid: str) -> dict[str, Any] | None:
    return next((p for p in projects() if p["id"] == pid), None)


def save_project(name: str, notes: str = "", pid: str = "") -> dict[str, Any]:
    ps = projects()
    name = re.sub(r"\s+", " ", name).strip()[:60] or "Project"
    old = next((p for p in ps if p["id"] == pid), None) if pid else None
    if old:
        old.update(name=name, notes=notes.strip()[:4000])
    else:
        old = {"id": new_id(), "name": name, "notes": notes.strip()[:4000], "created": time.time()}
        ps.append(old)
    save_json(PROJECTS, ps)
    return old


# ---------- project files: syllabus, notes, slides - read once, searched per question ----------

FILE_TYPES = (".pdf", ".docx", ".pptx", ".txt", ".md", ".csv")
MAX_FILE = 30 * 1024 * 1024


def _pdir(pid: str) -> Path:
    return DATA / "projects" / Path(pid).name


def _safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", Path(name).name).strip(" .") or "file"
    return name[:120]


def project_files(pid: str) -> list[dict[str, Any]]:
    d = _pdir(pid) / "files"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.iterdir(), key=lambda f: f.stat().st_mtime):
        t = _pdir(pid) / "text" / (f.name + ".txt")
        out.append({"name": f.name, "size": f.stat().st_size,
                    "chars": t.stat().st_size if t.exists() else 0})
    return out


def add_file(pid: str, name: str, data: bytes) -> dict[str, Any]:
    """Keep a copy of the file in the project and its text beside it. Raises
    ValueError with a reason to show (wrong type, too big, nothing readable)."""
    if not project(pid):
        raise ValueError("No such project.")
    name = _safe_name(name)
    if Path(name).suffix.lower() not in FILE_TYPES:
        raise ValueError(f"{name}: only PDF, Word, PowerPoint and text files can be read.")
    if len(data) > MAX_FILE:
        raise ValueError(f"{name}: over 30 MB.")
    files, texts = _pdir(pid) / "files", _pdir(pid) / "text"
    files.mkdir(parents=True, exist_ok=True)
    texts.mkdir(parents=True, exist_ok=True)
    (files / name).write_bytes(data)
    from .tools.knowledge import _extract
    text = (_extract(files / name) or "").strip()
    # Slides that are pictures and scanned pages have little or no text layer:
    # read the pictures with Windows OCR too.
    if Path(name).suffix.lower() in (".pptx", ".pdf", ".docx") and (
            len(text) < 1500 or (len(data) > 3_000_000 and len(text) < 5000)):
        seen = _ocr_file(files / name)
        if len(seen) > 40:
            text = (text + "\n\n[text read from the pictures]\n" + seen).strip()
    if len(text) < 20:
        (files / name).unlink(missing_ok=True)
        raise ValueError(f"{name}: no text in it that I can read (a scanned PDF?).")
    (texts / (name + ".txt")).write_text(text, encoding="utf-8")
    return {"name": name, "size": len(data), "chars": len(text)}


def _ocr_file(path: Path) -> str:
    """Text in the pictures of a file - image-only slides, scanned PDF pages -
    by Windows OCR (ocr.py). Icons and logos are skipped; at most 80 pictures."""
    import asyncio
    import io
    import zipfile

    from PIL import Image

    from . import ocr
    ext = path.suffix.lower()
    images: list[bytes] = []
    try:
        if ext in (".pptx", ".docx"):
            prefix = "ppt/media/" if ext == ".pptx" else "word/media/"
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.startswith(prefix)
                         and n.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"))]
                names.sort(key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)])
                images = [z.read(n) for n in names]
        elif ext == ".pdf":
            from pypdf import PdfReader
            for page in PdfReader(str(path)).pages:
                images += [im.data for im in page.images]
    except Exception as e:
        print(f"[ocr file: {e}]")
    out = []
    for data in images[:80]:
        try:
            im = Image.open(io.BytesIO(data))
            if im.width < 300 or im.height < 150:
                continue
            im.thumbnail((2400, 2400))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "PNG")
            t = asyncio.run(ocr._read(buf.getvalue())).strip()
            if len(t) > 20:
                out.append(t)
        except Exception:
            continue
    return "\n\n".join(out)


def remove_file(pid: str, name: str) -> bool:
    """Into the recycle folder with the deleted chats, like everything else here."""
    name = Path(name).name
    f = _pdir(pid) / "files" / name
    if not f.exists():
        return False
    TRASH.mkdir(exist_ok=True)
    f.replace(TRASH / f"{Path(pid).name}-{name}")
    (_pdir(pid) / "text" / (name + ".txt")).unlink(missing_ok=True)
    return True


_STOP = {"the", "and", "what", "when", "where", "which", "who", "how", "for", "this", "that", "with", "from",
         "explain", "about", "does", "are", "you", "can", "tell", "answer", "line", "one", "please", "give",
         "show", "athena", "is", "was", "will", "have", "has", "my", "your", "our", "its", "into", "out"}


_VOCAB: dict[str, tuple[float, set[str]]] = {}


def _terms(pid: str, question: str) -> list[str]:
    """The question's telling words, typos mended against the files' own
    words: "how many unts in my coure plan" -> units, course, plan."""
    import difflib
    tdir = _pdir(pid) / "text"
    stamp = max((f.stat().st_mtime for f in tdir.glob("*.txt")), default=0.0) if tdir.is_dir() else 0.0
    if _VOCAB.get(pid, (None,))[0] != stamp:
        vocab: set[str] = set()
        for f in tdir.glob("*.txt") if tdir.is_dir() else []:
            vocab |= set(re.findall(r"[a-z0-9]{3,}", f.read_text(encoding="utf-8", errors="replace").lower()))
        _VOCAB[pid] = (stamp, vocab)
    vocab = _VOCAB[pid][1]
    out = []
    for w in re.findall(r"[a-z0-9]{3,}", question.lower()):
        if w in _STOP:
            continue
        if w not in vocab and not w.isdigit():
            near = difflib.get_close_matches(w, vocab, n=1, cutoff=0.75)
            w = near[0] if near else w
        if w not in out:
            out.append(w)
    return out


def relevant(pid: str, question: str, budget: int = 5000) -> str:
    """The parts of the project's files that bear on the question. Small
    projects go in whole; bigger ones are cut into passages scored by the
    question's words (rarer words count more).
    ponytail: keyword scoring, not embeddings - fine for syllabi and notes;
    use db.py's nomic-embed if questions start missing paraphrased passages."""
    tdir = _pdir(pid) / "text"
    if not tdir.is_dir():
        return ""
    docs = [(f.name[:-4], f.read_text(encoding="utf-8", errors="replace")) for f in sorted(tdir.glob("*.txt"))]
    if not docs:
        return ""
    if sum(len(t) for _, t in docs) <= budget:
        return "\n\n".join(f"[{n}]\n{t}" for n, t in docs)
    passages = []
    for n, t in docs:
        for i in range(0, len(t), 1000):
            passages.append((n, t[max(0, i - 100):i + 1100]))
    words = _terms(pid, question)
    if not words:
        return "\n\n".join(f"[{n}]\n{p}" for n, p in passages[:budget // 1200])
    import math
    low = [p.lower() for _, p in passages]
    idf = {w: math.log(1 + len(low) / (1 + sum(w in p for p in low))) for w in set(words)}
    scored = sorted(range(len(passages)), key=lambda i: -sum(low[i].count(w) * idf[w] for w in set(words)))
    out, used = [], 0
    for i in scored:
        if used + len(passages[i][1]) > budget or not any(w in low[i] for w in words):
            continue
        out.append(f"[{passages[i][0]}]\n{passages[i][1]}")
        used += len(passages[i][1])
    return "\n\n".join(out)


def covers(pid: str, question: str) -> bool:
    """The project's files clearly bear on the question: one passage holds at
    least two of its telling words (or one, for a one-word question)."""
    tdir = _pdir(pid) / "text"
    if not tdir.is_dir():
        return False
    words = set(_terms(pid, question))
    if not words:
        return False
    need = 1 if len(words) == 1 else 2
    for f in tdir.glob("*.txt"):
        t = f.read_text(encoding="utf-8", errors="replace").lower()
        for i in range(0, len(t), 1000):
            if sum(w in t[max(0, i - 100):i + 1100] for w in words) >= need:
                return True
    return False


def delete_project(pid: str) -> bool:
    """The project goes; its chats stay, back in the main list."""
    ps = projects()
    if not any(p["id"] == pid for p in ps):
        return False
    save_json(PROJECTS, [p for p in ps if p["id"] != pid])
    for c in listing():
        if c.get("project") == pid:
            update(c["id"], project="")
    return True


TRASH = DATA / "chats_trash"     # deleted chats, kept 30 days so a wrong tap can be undone


def delete(chat_id: str) -> bool:
    """Into the recycle folder, not erased: one tap deletes, so Undo must work."""
    p = _path(chat_id)
    if not p.exists():
        return False
    TRASH.mkdir(exist_ok=True)
    p.replace(TRASH / p.name)
    for old in TRASH.glob("*.json"):                       # tidy: older than 30 days
        if time.time() - old.stat().st_mtime > 30 * 86400:
            old.unlink(missing_ok=True)
    return True


def restore(chat_id: str) -> bool:
    t = TRASH / f"{Path(chat_id).name}.json"
    if not t.exists():
        return False
    t.replace(_path(chat_id))
    return True


def when(stamp: float) -> str:
    """"14:32", "Yesterday" or a date - whichever identifies it fastest."""
    if not stamp:
        return ""
    now = time.localtime()
    then = time.localtime(stamp)
    if then[:3] == now[:3]:
        return time.strftime("%H:%M", then)
    if (time.time() - stamp) < 48 * 3600:
        return "Yesterday"
    if then.tm_year == now.tm_year:
        return time.strftime("%d %b", then)
    return time.strftime("%d %b %y", then)
