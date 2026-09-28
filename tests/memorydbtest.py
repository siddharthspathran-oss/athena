"""Checks data/athena.db (jarvis/db.py): importing the old index files, search
by meaning and by exact words, the fallback when Ollama is down, and
several writers at once. Runs against a scratch folder, with a stand-in for
the embedding model, so it needs neither Ollama nor your real data.

    python tests/memorydbtest.py
"""
import json
import re
import sys
import tempfile
import threading
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import db  # noqa: E402

DIM = 64


def fake_embed(text: str) -> np.ndarray:
    """Bag of words hashed into DIM buckets: texts sharing words point the
    same way, which is all these checks need from an embedding."""
    v = np.zeros(DIM, dtype=np.float32)
    for w in re.findall(r"[a-z]{3,}", text.lower()):
        v[zlib.crc32(w.encode()) % DIM] += 1
    n = np.linalg.norm(v)
    return v / n if n else v


def fake_embed_many(texts):
    return np.vstack([fake_embed(t) for t in texts])


def use_folder(folder: Path) -> None:
    db.DB_FILE = folder / "athena.db"
    db._vec_cache.clear()


def check_migration(bad: list[str]) -> None:
    tmp = Path(tempfile.mkdtemp())
    docs = [{"path": "C:/notes/lease.pdf", "name": "lease.pdf", "chunk": 0,
             "text": "The tenant must give two months notice before leaving."},
            {"path": "C:/notes/syllabus.pdf", "name": "syllabus.pdf", "chunk": 0,
             "text": "MATH2065 covers probability, Poisson processes and Markov chains."}]
    np.savez_compressed(tmp / "index.npz", vectors=fake_embed_many([d["text"] for d in docs]))
    (tmp / "index_meta.json").write_text(json.dumps(docs), encoding="utf-8")
    chats = [{"sid": "c1", "title": "store", "when": 1.0,
              "text": "You: where is order RGZ1004\nAthena: shipped with Delhivery"}]
    np.savez_compressed(tmp / "chat_index.npz", v=fake_embed_many([c["text"] for c in chats]))
    (tmp / "chat_index.json").write_text(json.dumps({"done": {"c1": 2}, "chunks": chats}),
                                         encoding="utf-8")
    (tmp / "voice_history.jsonl").write_text(
        json.dumps({"t": 5.0, "role": "user", "content": "play believer"}) + "\n"
        + "not json\n", encoding="utf-8")
    use_folder(tmp)

    if db.count("doc") != 2 or db.count("chat") != 1:
        bad.append(f"migration: doc={db.count('doc')} chat={db.count('chat')}, want 2 and 1")
    if db.progress("chat") != {"c1": 2}:
        bad.append(f"migration lost chat progress: {db.progress('chat')}")
    if [r["content"] for r in db.rows("voice_log")] != ["play believer"]:
        bad.append(f"migration of voice history: {db.rows('voice_log')}")
    left = sorted(p.name for p in tmp.iterdir() if not p.name.startswith("athena.db"))
    want = sorted(["chat_index.json.migrated", "chat_index.npz.migrated", "index.npz.migrated",
                   "index_meta.json.migrated", "voice_history.jsonl.migrated"])
    if left != want:
        bad.append(f"old files not kept as .migrated: {left}")

    # Opening again must not import anything twice.
    db._ready["path"] = None
    if db.count("doc") != 2:
        bad.append("reopening the database imported the documents again")


def check_search(bad: list[str]) -> None:
    use_folder(Path(tempfile.mkdtemp()))
    rows = [
        {"source": "a.pdf", "title": "a.pdf", "text": "Kirchhoff's voltage law: the sum of voltages around a loop is zero."},
        {"source": "b.pdf", "title": "b.pdf", "text": "Order RGZ1004 was shipped by Delhivery, tracking DLV123."},
        {"source": "c.pdf", "title": "c.pdf", "text": "Locker 42 is on the second floor near the library."},
        {"source": "d.pdf", "title": "d.pdf", "text": "Butterworth filters have a maximally flat passband."},
    ]
    db.add_chunks("doc", rows, fake_embed_many([r["text"] for r in rows]))

    hits = db.search("doc", "sum of voltages around a loop", k=2, embed=fake_embed)
    if not hits or hits[0]["source"] != "a.pdf":
        bad.append(f"meaning search: {[h['source'] for h in hits]}")

    # Exact tokens: an embedding of "RGZ1004" is noise, the keyword index is not.
    hits = db.search("doc", "RGZ1004", k=2, embed=fake_embed)
    if not hits or hits[0]["source"] != "b.pdf":
        bad.append(f"exact-word search for RGZ1004: {[h['source'] for h in hits]}")

    # Embedding unavailable: still answers, by keyword.
    def broken(_):
        raise RuntimeError("ollama is not running")
    hits = db.search("doc", "where is locker 42", k=2, embed=broken)
    if not hits or hits[0]["source"] != "c.pdf":
        bad.append(f"search with the embedder down: {[h['source'] for h in hits]}")

    # A question about nothing indexed returns nothing, not the least-bad match.
    hits = db.search("doc", "zebra migration patterns", k=3, embed=fake_embed, min_score=0.45)
    if hits:
        bad.append(f"unrelated question matched: {[h['source'] for h in hits]}")

    # The vector cache notices new rows.
    db.add_chunks("doc", [{"source": "e.pdf", "title": "e.pdf", "text": "Thevenin equivalent circuits"}],
                  fake_embed_many(["Thevenin equivalent circuits"]))
    hits = db.search("doc", "thevenin equivalent", k=1, embed=fake_embed)
    if not hits or hits[0]["source"] != "e.pdf":
        bad.append("a chunk added after the first search was not found")

    # FTS syntax in a question must not break the query.
    try:
        db.search("doc", 'what about "AND" OR NEAR(x) - y*', k=2, embed=fake_embed)
    except Exception as e:
        bad.append(f"punctuation in a question broke search: {e}")

    db.clear("doc")
    if db.count("doc") or db.search("doc", "RGZ1004", k=2, embed=fake_embed):
        bad.append("clear left chunks behind (or in the keyword index)")


def check_history(bad: list[str]) -> None:
    use_folder(Path(tempfile.mkdtemp()))

    def writer(n):
        for i in range(25):
            db.log("journal", "user", f"thread {n} line {i}")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = db.rows("journal")
    if len(rows) != 100:
        bad.append(f"concurrent writers: {len(rows)} of 100 lines kept")
    db.drop_rows("journal", rows[49]["id"])
    if len(db.rows("journal")) != 50:
        bad.append("drop_rows removed the wrong lines")
    db.log("nonsense_table", "user", "x")          # ignored, never raises


def check_recall(bad: list[str]) -> None:
    """recall.search end to end: chats on disk -> indexed -> found."""
    from jarvis import recall

    tmp = Path(tempfile.mkdtemp())
    use_folder(tmp)
    (tmp / "chats").mkdir()
    (tmp / "chats" / "c1.json").write_text(json.dumps({"title": "filters", "messages": [
        {"role": "user", "content": "which filter should I use for the audio project"},
        {"role": "assistant", "content": "A Butterworth filter, fourth order, cutoff 3 kHz."}]}),
        encoding="utf-8")
    saved = recall.DATA, recall._embed
    recall.DATA = tmp
    recall._embed = lambda texts, kind: fake_embed_many(texts)
    try:
        found = recall.search("butterworth filter audio")
        if "Butterworth" not in found:
            bad.append(f"recall.search missed the chat: {found[:120]!r}")
        before = db.count("chat")
        recall.update_index()
        if db.count("chat") != before:
            bad.append("re-indexing with nothing new added chunks again")
    finally:
        recall.DATA, recall._embed = saved


if __name__ == "__main__":
    original = db.DB_FILE
    bad: list[str] = []
    try:
        for check in (check_migration, check_search, check_history, check_recall):
            check(bad)
    finally:
        use_folder(original.parent)
    for b in bad:
        print("FAIL", b)
    print(f"4 groups of checks, fts5={'yes' if db._ready['fts'] else 'no'}, {len(bad)} failure(s)")
    sys.exit(1 if bad else 0)
