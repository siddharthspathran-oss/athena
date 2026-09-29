"""data/athena.db - the searchable half of Athena's memory, in one SQLite file.

What lives here: the chunks behind "ask my documents" and "what did we talk
about", each with its embedding and a full-text index, plus the spoken
history and the learner's journal. What does not: memory.json and
profile.md, which are small and meant to be read and edited by hand.

Why SQLite rather than the .npz + .json pairs this replaces:

  * Adding ten chunks rewrote and recompressed the whole matrix, so every
    update got slower as history grew. Here new rows are appended.
  * The pair was written as two separate files, not atomically. A crash
    between them left them out of step, and the next search threw the whole
    index away to rebuild. A transaction is all or nothing.
  * Voice, the HUD and the popup are separate processes that all write
    history. WAL mode lets them, where appending to a shared file is luck.
  * Embeddings rank by meaning and are weak on exact tokens - "locker 42",
    "RGZ1004", "MATH2065", "Q6" - which are what gets looked up later. FTS5
    finds those, and search() merges both rankings.

Nothing runs in the background and there is no server: sqlite3 ships with
Python. Vectors are kept in memory per kind after the first search and
reloaded only when rows change; a brute-force dot product over tens of
thousands of them takes milliseconds, so a vector database would buy nothing.

The first open imports the old files (index.npz, chat_index.npz,
voice_history.jsonl, journal.jsonl) and renames them *.migrated, so nothing
has to be embedded again and the originals are kept.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from .config import DATA

DB_FILE = DATA / "athena.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id     INTEGER PRIMARY KEY,
    kind   TEXT NOT NULL,          -- 'doc' (documents) or 'chat' (conversations)
    source TEXT NOT NULL,          -- file path, or chat id / voice-YYYY-MM-DD
    title  TEXT NOT NULL DEFAULT '',
    pos    INTEGER NOT NULL DEFAULT 0,
    at     REAL NOT NULL DEFAULT 0,
    text   TEXT NOT NULL,
    vec    BLOB
);
CREATE INDEX IF NOT EXISTS chunks_kind ON chunks(kind, source);
CREATE TABLE IF NOT EXISTS progress (
    kind   TEXT NOT NULL,
    source TEXT NOT NULL,
    done   INTEGER NOT NULL,
    PRIMARY KEY (kind, source)
);
CREATE TABLE IF NOT EXISTS voice_log (
    id INTEGER PRIMARY KEY, t REAL NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY, t REAL NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL
);
"""

_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, title, kind UNINDEXED, content='chunks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, title, kind) VALUES (new.id, new.text, new.title, new.kind);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, title, kind)
    VALUES ('delete', old.id, old.text, old.title, old.kind);
END;
"""

_ready_lock = threading.Lock()
_ready = {"path": None, "fts": False}


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=10)
    con.execute("PRAGMA journal_mode=WAL")      # readers never block the writer
    con.execute("PRAGMA synchronous=NORMAL")    # safe with WAL, far fewer fsyncs
    con.execute("PRAGMA busy_timeout=10000")
    return con


def _prepare(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    try:
        con.executescript(_FTS)
        _ready["fts"] = True
    except sqlite3.OperationalError:
        # A Python built without FTS5: search falls back to LIKE.
        _ready["fts"] = False
    con.commit()
    # Voice, the HUD and the popup start together, and each would run the
    # one-time import: three processes found the index empty at once and
    # imported every conversation three times. BEGIN IMMEDIATE takes the
    # write lock first, so one imports and the others wait, then find it done.
    con.execute("BEGIN IMMEDIATE")
    try:
        imported = _migrate(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    # Renamed only once the rows are safely in; a file that cannot be renamed
    # (still open elsewhere) is imported no second time - the checks above see
    # the rows already there.
    for p in imported:
        try:
            p.rename(p.with_name(p.name + ".migrated"))
        except OSError:
            pass


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """A connection with the schema in place; commits on success.

    One per call rather than one shared: sqlite3 connections belong to the
    thread that made them, and opening a local file costs microseconds.
    """
    path = DB_FILE
    with _ready_lock:
        if _ready["path"] != path:
            path.parent.mkdir(parents=True, exist_ok=True)
            con = _connect(path)
            try:
                _prepare(con)
            finally:
                con.close()
            _ready["path"] = path
    con = _connect(path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def has_fts() -> bool:
    with connect():
        return bool(_ready["fts"])


# ---------------------------------------------------------------- chunks

def _blob(vec) -> bytes | None:
    return None if vec is None else np.asarray(vec, dtype=np.float32).tobytes()


def add_chunks(kind: str, rows: list[dict], vecs: np.ndarray | None,
               progress: dict[str, int] | None = None) -> int:
    """Store chunks (source, title, pos, at, text) with their vectors, and the
    per-source progress that says how far they reach - in one transaction,
    so the two can never disagree."""
    with connect() as con:
        todo = list(enumerate(rows))
        if progress is not None:
            # Incremental indexing runs in voice, the HUD and the popup, each
            # with its own in-process lock only. Two of them could read the
            # same progress, embed the same turns and both store them (83
            # duplicate chunks in a test). Take the write lock, then skip
            # what another process stored meanwhile.
            con.execute("BEGIN IMMEDIATE")
            have = {(s, p) for s, p in con.execute(
                "SELECT source, pos FROM chunks WHERE kind=?", (kind,))}
            todo = [(i, r) for i, r in todo
                    if (str(r.get("source", "")), int(r.get("pos", 0))) not in have]
        con.executemany(
            "INSERT INTO chunks(kind, source, title, pos, at, text, vec) VALUES (?,?,?,?,?,?,?)",
            [(kind, str(r.get("source", "")), str(r.get("title", "")), int(r.get("pos", 0)),
              float(r.get("at", 0)), str(r["text"]),
              _blob(vecs[i]) if vecs is not None else None)
             for i, r in todo])
        for source, done in (progress or {}).items():
            con.execute("INSERT INTO progress(kind, source, done) VALUES (?,?,?) "
                        "ON CONFLICT(kind, source) DO UPDATE SET done=MAX(done, excluded.done)",
                        (kind, source, int(done)))
    return len(todo)


def progress(kind: str) -> dict[str, int]:
    with connect() as con:
        return {s: d for s, d in con.execute(
            "SELECT source, done FROM progress WHERE kind=?", (kind,))}


def sources(kind: str) -> set[str]:
    with connect() as con:
        return {s for (s,) in con.execute("SELECT DISTINCT source FROM chunks WHERE kind=?", (kind,))}


def count(kind: str) -> int:
    with connect() as con:
        return con.execute("SELECT COUNT(*) FROM chunks WHERE kind=?", (kind,)).fetchone()[0]


def titles(kind: str) -> list[str]:
    with connect() as con:
        return [t for (t,) in con.execute(
            "SELECT DISTINCT title FROM chunks WHERE kind=? ORDER BY title", (kind,))]


def clear(kind: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM chunks WHERE kind=?", (kind,))
        con.execute("DELETE FROM progress WHERE kind=?", (kind,))
    _vec_cache.pop(kind, None)


def size_mb() -> float:
    try:
        return sum(p.stat().st_size for p in DB_FILE.parent.glob(DB_FILE.name + "*")) / 1e6
    except OSError:
        return 0.0


# ---------------------------------------------------------------- search

_vec_cache: dict[str, tuple] = {}


def _vectors(con: sqlite3.Connection, kind: str) -> tuple[np.ndarray, np.ndarray | None]:
    """(ids, matrix) for every embedded chunk of a kind, cached until rows change."""
    sig = con.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM chunks "
                      "WHERE kind=? AND vec IS NOT NULL", (kind,)).fetchone()
    hit = _vec_cache.get(kind)
    if hit and hit[0] == sig and hit[3] == DB_FILE:
        return hit[1], hit[2]
    ids, rows = [], []
    for i, blob in con.execute("SELECT id, vec FROM chunks WHERE kind=? AND vec IS NOT NULL "
                               "ORDER BY id", (kind,)):
        ids.append(i)
        rows.append(np.frombuffer(blob, dtype=np.float32))
    dims = {len(r) for r in rows}
    if len(dims) > 1:                       # a model change mid-index: keep the common size
        common = max(dims, key=lambda d: sum(len(r) == d for r in rows))
        keep = [n for n, r in enumerate(rows) if len(r) == common]
        ids, rows = [ids[n] for n in keep], [rows[n] for n in keep]
    matrix = np.vstack(rows) if rows else None
    out = np.array(ids, dtype=np.int64), matrix
    _vec_cache[kind] = (sig, out[0], out[1], DB_FILE)
    return out


_TOKEN = re.compile(r"[\w]+", re.UNICODE)


def _fts_query(text: str) -> str:
    """Every word of the question as a quoted term, any of them may match.
    Quoting keeps FTS5 from reading "and", "or", "near" or a hyphen as syntax."""
    words = [w for w in _TOKEN.findall(text.lower()) if len(w) > 1]
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))


def _keyword(con: sqlite3.Connection, kind: str, query: str, limit: int) -> list[int]:
    if _ready["fts"]:
        q = _fts_query(query)
        if not q:
            return []
        try:
            return [r for (r,) in con.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND kind=? "
                "ORDER BY bm25(chunks_fts) LIMIT ?", (q, kind, limit))]
        except sqlite3.OperationalError:
            return []
    words = [w for w in _TOKEN.findall(query.lower()) if len(w) > 2][:6]
    if not words:
        return []
    where = " OR ".join("lower(text) LIKE ?" for _ in words)
    return [r for (r,) in con.execute(
        f"SELECT id FROM chunks WHERE kind=? AND ({where}) LIMIT ?",
        (kind, *[f"%{w}%" for w in words], limit))]


def search(kind: str, query: str, k: int = 5,
           embed: Callable[[str], np.ndarray] | None = None,
           min_score: float = 0.0) -> list[dict]:
    """The k best chunks for `query`, by meaning and by exact words together.

    Each ranking is turned into reciprocal-rank points (1 / (60 + rank)) and
    the points added, so a chunk both lists agree on comes first and neither
    list's raw scores need to be comparable. A chunk found only by meaning
    must score at least min_score, so a question about nothing that was ever
    said does not return the least-bad match; a chunk that contains the
    exact words is always a candidate.

    Without an embedder (Ollama not running, model not pulled) this is a
    keyword search rather than no search at all.
    """
    with connect() as con:
        points: dict[int, float] = {}
        cosine: dict[int, float] = {}
        if embed is not None:
            ids, matrix = _vectors(con, kind)
            if matrix is not None and len(ids):
                try:
                    q = np.asarray(embed(query), dtype=np.float32)
                except Exception:
                    q = None
                if q is not None and q.shape[0] == matrix.shape[1]:
                    scores = matrix @ q
                    order = np.argsort(-scores)[: max(k * 4, 20)]
                    rank = 0
                    for i in order:
                        if scores[i] < min_score:
                            break
                        cid = int(ids[i])
                        cosine[cid] = float(scores[i])
                        points[cid] = points.get(cid, 0) + 1 / (60 + rank)
                        rank += 1
        for rank, cid in enumerate(_keyword(con, kind, query, max(k * 4, 20))):
            points[cid] = points.get(cid, 0) + 1 / (60 + rank)
        best = sorted(points, key=lambda c: -points[c])[:k]
        if not best:
            return []
        rows = {r[0]: r for r in con.execute(
            f"SELECT id, source, title, pos, at, text FROM chunks WHERE id IN "
            f"({','.join('?' * len(best))})", best)}
    return [{"id": c, "source": rows[c][1], "title": rows[c][2], "pos": rows[c][3],
             "at": rows[c][4], "text": rows[c][5], "score": cosine.get(c),
             "keyword": c not in cosine} for c in best if c in rows]


# ---------------------------------------------------------------- history

def log(table: str, role: str, content: str) -> None:
    """Append one spoken/popup turn to voice_log or journal. Never raises:
    losing a line of history must not cost a reply."""
    if table not in ("voice_log", "journal") or role not in ("user", "assistant") or not content:
        return
    try:
        with connect() as con:
            con.execute(f"INSERT INTO {table}(t, role, content) VALUES (?,?,?)",
                        (time.time(), role, content[:1500]))
    except sqlite3.Error:
        pass


def rows(table: str) -> list[dict]:
    if table not in ("voice_log", "journal"):
        return []
    with connect() as con:
        return [{"id": i, "t": t, "role": r, "content": c} for i, t, r, c in con.execute(
            f"SELECT id, t, role, content FROM {table} ORDER BY id")]


def drop_rows(table: str, upto_id: int) -> None:
    if table in ("voice_log", "journal"):
        with connect() as con:
            con.execute(f"DELETE FROM {table} WHERE id <= ?", (upto_id,))


def last_time(table: str) -> float | None:
    if table not in ("voice_log", "journal"):
        return None
    with connect() as con:
        return con.execute(f"SELECT MAX(t) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------- migration

def _migrate(con: sqlite3.Connection) -> list[Path]:
    """Bring the old files in, once. Each is renamed *.migrated afterwards,
    kept rather than deleted; a file that cannot be read is left alone."""
    data = DB_FILE.parent

    def empty(kind: str) -> bool:
        # Imported already (a rename that failed last time): just rename now.
        return con.execute("SELECT COUNT(*) FROM chunks WHERE kind=?", (kind,)).fetchone()[0] == 0

    imported: list[Path] = []

    def done(*paths: Path) -> None:
        imported.extend(paths)

    # Documents: index.npz + index_meta.json
    npz, meta_f = data / "index.npz", data / "index_meta.json"
    if npz.exists() and meta_f.exists():
        try:
            vecs = np.load(npz)["vectors"]
            meta = json.loads(meta_f.read_text(encoding="utf-8"))
            if len(vecs) == len(meta) and empty("doc"):
                _insert_old(con, "doc", [
                    {"source": m.get("path", ""), "title": m.get("name", ""),
                     "pos": m.get("chunk", 0), "at": 0, "text": m.get("text", "")} for m in meta], vecs)
            done(npz, meta_f)
        except Exception as e:
            print(f"[db: could not import the document index ({e}) - left as it was]")

    # Conversations: chat_index.npz + chat_index.json
    npz, meta_f = data / "chat_index.npz", data / "chat_index.json"
    if npz.exists() and meta_f.exists():
        try:
            vecs = np.load(npz)["v"]
            meta = json.loads(meta_f.read_text(encoding="utf-8"))
            chunks = meta.get("chunks", [])
            if len(vecs) == len(chunks) and empty("chat"):
                _insert_old(con, "chat", [
                    {"source": c.get("sid", ""), "title": c.get("title", ""), "pos": 0,
                     "at": c.get("when", 0), "text": c.get("text", "")} for c in chunks], vecs)
                con.executemany("INSERT OR REPLACE INTO progress(kind, source, done) VALUES ('chat',?,?)",
                                [(s, int(d)) for s, d in (meta.get("done") or {}).items()])
            done(npz, meta_f)
        except Exception as e:
            print(f"[db: could not import the conversation index ({e}) - left as it was]")

    # Spoken history and the learner's journal
    for name, table in (("voice_history.jsonl", "voice_log"), ("journal.jsonl", "journal")):
        f = data / name
        if not f.exists():
            continue
        try:
            rows_in = []
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("role") in ("user", "assistant") and r.get("content"):
                    rows_in.append((float(r.get("t", 0)), r["role"], str(r["content"])[:1500]))
            if con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0:
                con.executemany(f"INSERT INTO {table}(t, role, content) VALUES (?,?,?)", rows_in)
            done(f)
        except Exception as e:
            print(f"[db: could not import {name} ({e}) - left as it was]")
    return imported


def _insert_old(con: sqlite3.Connection, kind: str, rows_in: list[dict], vecs) -> None:
    con.executemany(
        "INSERT INTO chunks(kind, source, title, pos, at, text, vec) VALUES (?,?,?,?,?,?,?)",
        [(kind, str(r["source"]), str(r["title"]), int(r["pos"]), float(r["at"]), str(r["text"]),
          _blob(vecs[i])) for i, r in enumerate(rows_in)])
