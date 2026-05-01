#!/usr/bin/env python3
"""Build SQLite FTS5 + (optional) sqlite-vec index of docs/**/*.md and wiki/**/*.md.

Storage: .gowth-mem/index.db in workspace root.

Schema:
  chunks(id PK, path, heading, content, mtime, hash)
  chunks_fts (FTS5 virtual over chunks.content; BM25 native)
  chunks_vec (sqlite-vec virtual; only created if sqlite-vec importable AND embedding key)

Usage:
  python3 _index.py [--full] [--workspace PATH]

  --full         Drop and rebuild everything.
  Otherwise: incremental (only re-index files whose mtime changed).

Graceful fallback:
- If FTS5 unavailable in this Python's sqlite3 → exit with WARNING.
- If sqlite-vec not installed → skip vector table; FTS5-only index still built.
- If embedding API key missing → skip vector embeddings; FTS5-only.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import struct
import sys
from pathlib import Path

# Lazy imports - guarded
try:
    import sqlite_vec  # type: ignore
    HAS_VEC = True
except ImportError:
    HAS_VEC = False

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _embed import embed_one, detect_provider  # type: ignore
    HAS_EMBED_MODULE = True
except ImportError:
    HAS_EMBED_MODULE = False
from _paths import docs_root  # type: ignore

CHUNK_SIZE = 1500


def split_chunks(text: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    heading = ""
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.+?)\s*#*$", line)
        if m:
            if buf:
                chunks.append((heading, "\n".join(buf).strip()))
                buf = []
            heading = m.group(2)
        else:
            buf.append(line)
    if buf:
        chunks.append((heading, "\n".join(buf).strip()))
    out: list[tuple[str, str]] = []
    for h, c in chunks:
        if not c:
            continue
        if len(c) <= CHUNK_SIZE:
            out.append((h, c))
        else:
            for i in range(0, len(c), CHUNK_SIZE):
                out.append((h, c[i:i + CHUNK_SIZE]))
    return out


def serialize_vec(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--workspace", default=None)
    args = ap.parse_args()
    workspace = Path(args.workspace or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    idx_dir = workspace / ".gowth-mem"
    idx_dir.mkdir(exist_ok=True)
    db_path = idx_dir / "index.db"
    db = sqlite3.connect(db_path)

    # FTS5 availability check
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_test USING fts5(x)")
        db.execute("DROP TABLE _fts5_test")
    except sqlite3.OperationalError as e:
        print(f"ERROR: SQLite FTS5 not available in this Python build: {e}", file=sys.stderr)
        db.close()
        return 1

    # Determine vector availability
    provider_info = None
    sample_dim = 0
    use_vec = False
    if HAS_VEC and HAS_EMBED_MODULE:
        provider_info = detect_provider()
        if provider_info:
            sample = embed_one("ping")
            if sample:
                sample_dim = len(sample)
                use_vec = True

    db.executescript("""
    CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL,
        heading TEXT,
        content TEXT NOT NULL,
        mtime REAL NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
    """)
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
        "content, content='chunks', content_rowid='id', tokenize='unicode61')"
    )

    if use_vec:
        sqlite_vec.load(db)  # type: ignore
        db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0("
            f"id INTEGER PRIMARY KEY, embedding FLOAT[{sample_dim}])"
        )

    if args.full:
        db.execute("DELETE FROM chunks_fts")
        db.execute("DELETE FROM chunks")
        if use_vec:
            db.execute("DELETE FROM chunks_vec")
        db.commit()

    sources: list[Path] = []
    docs = docs_root(workspace)
    if docs.is_dir():
        sources.extend(p for p in docs.rglob("*.md") if p.is_file())
    wiki = workspace / "wiki"
    if wiki.is_dir():
        sources.extend(p for p in wiki.rglob("*.md") if p.is_file())

    indexed_files = 0
    indexed_chunks = 0
    embed_calls = 0
    for f in sources:
        rel = str(f.relative_to(workspace))
        mtime = f.stat().st_mtime
        cur = db.execute("SELECT mtime FROM chunks WHERE path=? LIMIT 1", (rel,))
        row = cur.fetchone()
        if row and abs(row[0] - mtime) < 1e-6 and not args.full:
            continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        # Drop old chunks for this file
        old_ids = [r[0] for r in db.execute("SELECT id FROM chunks WHERE path=?", (rel,))]
        for oid in old_ids:
            db.execute("DELETE FROM chunks_fts WHERE rowid=?", (oid,))
            if use_vec:
                db.execute("DELETE FROM chunks_vec WHERE id=?", (oid,))
        db.execute("DELETE FROM chunks WHERE path=?", (rel,))
        for heading, content in split_chunks(text):
            h = hashlib.sha1(content.encode()).hexdigest()[:16]
            cid = db.execute(
                "INSERT INTO chunks (path, heading, content, mtime, hash) VALUES (?, ?, ?, ?, ?)",
                (rel, heading, content, mtime, h),
            ).lastrowid
            db.execute("INSERT INTO chunks_fts(rowid, content) VALUES (?, ?)", (cid, content))
            if use_vec:
                vec = embed_one(content)
                if vec:
                    db.execute(
                        "INSERT INTO chunks_vec(id, embedding) VALUES (?, ?)",
                        (cid, serialize_vec(vec)),
                    )
                    embed_calls += 1
            indexed_chunks += 1
        indexed_files += 1
    db.commit()
    db.close()

    print(f"indexed: {indexed_files} files, {indexed_chunks} chunks at .gowth-mem/index.db")
    if use_vec:
        print(f"vector: {embed_calls} embeddings via {provider_info[0]} (dim={sample_dim})")
    else:
        reason = []
        if not HAS_VEC:
            reason.append("sqlite-vec not installed")
        if not HAS_EMBED_MODULE or not (HAS_EMBED_MODULE and detect_provider()):
            reason.append("no embedding API key (OPENAI_API_KEY / VOYAGE_API_KEY / GEMINI_API_KEY)")
        print(f"vector: skipped ({'; '.join(reason) if reason else 'unknown'}) — FTS5-only index")
    return 0


if __name__ == "__main__":
    sys.exit(main())
