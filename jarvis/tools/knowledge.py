"""Ask questions about your own documents (local RAG).

Deliberately simple: documents are chunked, embedded with Ollama's
nomic-embed-text, and stored as a numpy matrix in data/index.npz.
For a personal document collection a brute-force cosine search is
instant and has no database to go wrong.
"""
from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path

import numpy as np

from ..config import DATA, cfg, expand
from ..registry import tool

TAG = ["knowledge"]

INDEX_FILE = DATA / "index.npz"
META_FILE = DATA / "index_meta.json"
EMBED_MODEL = "nomic-embed-text"


# ---------- text extraction ----------

# Some PDFs encode superscripts and subscripts in a private font mapping, and
# the text layer then comes out as letters from unrelated scripts: "3c³" reads
# back as "3cଷ" (Oriya SSA), "10c²" as "10cଶ". A model handed that is solving
# a different problem - the exponents are simply gone. The ranges below are
# sequential digit runs, anchored on characters seen standing for 1, 2, 3
# and 5 in one such file.
_MANGLED_DIGITS = {
    0x0B34: "0",  # Oriya run: U+0B35 was 1, U+0B36 2, U+0B37 3, U+0B39 5
    0x0C2C: "0",  # Telugu run, same trick, used for subscripts
}
_MANGLED_PAIRS = {"ቀ": "(", "ቁ": ")"}   # oversized brackets


def _demangle(text: str) -> str:
    """Undo a PDF's font-encoded digits, when they are clearly not real text.

    Only applied when these characters are rare specks in otherwise Latin
    text. A document genuinely written in Oriya is left alone.
    """
    odd = [c for c in text if 0x900 <= ord(c) <= 0x1400 and not c.isspace()]
    if not odd or len(odd) > max(20, len(text) * 0.05):
        return text

    out = []
    for ch in text:
        code = ord(ch)
        if ch in _MANGLED_PAIRS:
            out.append(_MANGLED_PAIRS[ch])
            continue
        for base in _MANGLED_DIGITS:
            if base <= code <= base + 9:
                digit = str(code - base)
                # These encode a raised digit. Directly after a symbol it is
                # an exponent, and "3c3" would read as three times c times
                # three; the same characters are also used for stacked
                # fractions, where the line break before them makes it clear
                # they are not powers.
                prev = next((c for c in reversed(out) if not c.isspace()), "")
                out.append("^" + digit if prev.isalpha() else digit)
                break
        else:
            try:
                out.append(str(unicodedata.digit(ch)))
            except (TypeError, ValueError):
                out.append(ch)
    return "".join(out)


def _extract(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader

            return _demangle("\n".join((pg.extract_text() or "")
                                       for pg in PdfReader(str(path)).pages))
        if ext == ".docx":
            import docx

            return _demangle("\n".join(p.text
                                       for p in docx.Document(str(path)).paragraphs))
        return _demangle(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""


def _chunk(text: str, size: int, overlap: int) -> list[str]:
    """Split on paragraph boundaries where possible, falling back to hard cuts."""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        if end < len(text):
            # prefer to break at a paragraph, then a sentence
            for sep in ("\n\n", ". ", "\n"):
                cut = text.rfind(sep, start + size // 2, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = max(end - overlap, start + 1)
    return chunks


# ---------- embeddings ----------

def _embed(texts: list[str]) -> np.ndarray:
    import ollama

    client = ollama.Client(host=cfg.get("llm.host", "http://127.0.0.1:11434"))
    resp = client.embed(model=EMBED_MODEL, input=texts)
    arr = np.array(resp.embeddings, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.clip(norms, 1e-9, None)


# ---------- index io ----------

def _load_index() -> tuple[np.ndarray | None, list[dict]]:
    if not INDEX_FILE.exists() or not META_FILE.exists():
        return None, []
    vecs = np.load(INDEX_FILE)["vectors"]
    meta = json.loads(META_FILE.read_text(encoding="utf-8"))
    return vecs, meta


@tool(tags=TAG)
def index_documents(folder: str = "", rebuild: bool = False) -> str:
    """Scan a folder and index its documents so you can ask questions about them.

    folder: folder to index, or empty to use knowledge.sources from config.yaml
    rebuild: true to discard the existing index and start fresh
    """
    sources = [folder] if folder else cfg.get("knowledge.sources", ["~/Documents"])
    exts = set(cfg.get("knowledge.extensions", [".txt", ".md", ".pdf", ".docx"]))
    size = int(cfg.get("knowledge.chunk_size", 1000))
    overlap = int(cfg.get("knowledge.chunk_overlap", 150))

    old_vecs, old_meta = (None, []) if rebuild else _load_index()
    seen = {m["path"] for m in old_meta}

    new_chunks: list[str] = []
    new_meta: list[dict] = []
    files_done = 0
    skip = {"node_modules", ".git", "AppData", "venv", "__pycache__"}

    for src in sources:
        root = expand(src)
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            if any(part in skip for part in p.parts):
                continue
            if str(p) in seen:
                continue
            text = _extract(p)
            if not text.strip():
                continue
            for i, ch in enumerate(_chunk(text, size, overlap)):
                new_chunks.append(ch)
                new_meta.append({"path": str(p), "name": p.name, "chunk": i, "text": ch})
            files_done += 1

    if not new_chunks:
        total = len(old_meta)
        return f"Nothing new to index. The index holds {total} chunk(s)."

    # embed in batches so a big folder does not blow up memory
    vecs_parts: list[np.ndarray] = []
    t0 = time.time()
    for i in range(0, len(new_chunks), 64):
        vecs_parts.append(_embed(new_chunks[i:i + 64]))
    new_vecs = np.vstack(vecs_parts)

    vectors = new_vecs if old_vecs is None else np.vstack([old_vecs, new_vecs])
    meta = old_meta + new_meta

    np.savez_compressed(INDEX_FILE, vectors=vectors)
    META_FILE.write_text(json.dumps(meta), encoding="utf-8")

    return (f"Indexed {files_done} new file(s) into {len(new_chunks)} chunk(s) "
            f"in {time.time() - t0:.0f}s. Index now holds {len(meta)} chunk(s).")


@tool(tags=TAG)
def search_documents(question: str, top_k: int = 0) -> str:
    """Search your indexed documents and return the most relevant passages.

    question: what you want to know
    top_k: how many passages to return, 0 to use the config default
    """
    vectors, meta = _load_index()
    if vectors is None or not meta:
        return "No document index yet. Run index_documents first."

    k = top_k or int(cfg.get("knowledge.top_k", 5))
    try:
        q = _embed([question])[0]
    except Exception as e:
        return f"ERROR: embedding failed ({e}). Run: ollama pull {EMBED_MODEL}"

    scores = vectors @ q
    top = np.argsort(-scores)[:k]

    out = []
    for rank, idx in enumerate(top, 1):
        m = meta[int(idx)]
        out.append(f"[{rank}] {m['name']} (relevance {scores[idx]:.2f})\n{m['text'][:700]}")
    return "\n\n---\n\n".join(out)


@tool(tags=TAG)
def index_status() -> str:
    """Report what is currently in the document index."""
    vectors, meta = _load_index()
    if vectors is None or not meta:
        return "The document index is empty. Run index_documents to build it."
    files = sorted({m["name"] for m in meta})
    preview = "\n".join(f"  {n}" for n in files[:20])
    more = f"\n  ... and {len(files) - 20} more" if len(files) > 20 else ""
    size_mb = INDEX_FILE.stat().st_size / 1e6
    return (f"{len(meta)} chunk(s) from {len(files)} file(s), {size_mb:.1f} MB on disk.\n"
            f"{preview}{more}")


@tool(tags=TAG, destructive=True)
def clear_index() -> str:
    """Delete the document index entirely."""
    for f in (INDEX_FILE, META_FILE):
        if f.exists():
            f.unlink()
    return "Document index cleared."
