#!/usr/bin/env python3
"""Build SQLite FTS5 + (optional) sqlite-vec index over global ~/.gowth-mem/.

v2.2 storage: ~/.gowth-mem/index.db (per-machine, gitignored).

Tables:
  chunks(id PK, path, heading, content, mtime, hash)
  chunks_fts (FTS5 virtual)
  chunks_vec (sqlite-vec virtual; only when sqlite-vec installed AND embedding key)
  slugs(workspace, slug, path, title, parents, status, last_touched, aliases)
    PRIMARY KEY (workspace, slug)

Sources:
  shared/                                 (workspace = "shared")
  workspaces/<ws>/{docs,topics,journal,skills}/  (workspace = <ws>)

Stored paths are relative to ~/.gowth-mem/.
WAL + busy_timeout so concurrent readers don't block writes.

Usage:
  python3 _index.py [--full]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import struct
import sys
from pathlib import Path

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
from _frontmatter import parse_file  # type: ignore
from _home import (  # type: ignore
    gowth_home,
    index_db,
    list_workspaces,
    shared_dir,
    workspace_dir,
)

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


def _collect_sources() -> list[tuple[str, Path]]:
    """Return [(workspace_label, path)] for every indexable .md file.

    workspace_label: "shared" for files under shared/, else the workspace name.
    """
    out: list[tuple[str, Path]] = []
    sd = shared_dir()
    if sd.is_dir():
        for p in sd.rglob("*.md"):
            if p.is_file():
                out.append(("shared", p))
    for ws in list_workspaces():
        wd = workspace_dir(ws)
        for sub in ("docs", "topics", "journal", "skills"):
            d = wd / sub
            if not d.is_dir():
                continue
            for p in d.rglob("*.md"):
                if p.is_file():
                    out.append((ws, p))
    return out


def _ensure_schema(db: sqlite3.Connection, sample_dim: int, use_vec: bool) -> None:
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

    CREATE TABLE IF NOT EXISTS slugs (
        workspace TEXT NOT NULL,
        slug TEXT NOT NULL,
        path TEXT NOT NULL,
        title TEXT,
        parents TEXT,
        status TEXT,
        last_touched TEXT,
        aliases TEXT,
        PRIMARY KEY (workspace, slug)
    );
    CREATE INDEX IF NOT EXISTS idx_slugs_path ON slugs(path);
    CREATE INDEX IF NOT EXISTS idx_slugs_status ON slugs(workspace, status);
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


def _index_slugs(db: sqlite3.Connection, sources: list[tuple[str, Path]], full: bool) -> int:
    """Refresh `slugs` from frontmatter scan. Returns count written."""
    if full:
        db.execute("DELETE FROM slugs")
    written = 0
    seen: set[tuple[str, str]] = set()
    for ws, path in sources:
        # Skip MOC files and registries.
        if path.name in {"_MAP.md", "_index.md", "files.md", "secrets.md", "tools.md"}:
            continue
        # Only frontmatter'd topic files contribute to slugs.
        fm, _ = parse_file(path)
        slug = fm.get("slug")
        if not slug:
            continue
        rel = str(path.relative_to(gowth_home()))
        title = str(fm.get("title") or "")
        status = str(fm.get("status") or "")
        last = str(fm.get("last_touched") or "")
        parents = fm.get("parents") or []
        aliases = fm.get("aliases") or []
        parents_s = ",".join(parents) if isinstance(parents, list) else str(parents)
        aliases_s = ",".join(aliases) if isinstance(aliases, list) else str(aliases)
        key = (ws, slug)
        if key in seen:
            continue
        seen.add(key)
        db.execute(
            "INSERT OR REPLACE INTO slugs (workspace, slug, path, title, parents, status, last_touched, aliases) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ws, slug, rel, title, parents_s, status, last, aliases_s),
        )
        written += 1
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    gh = gowth_home()
    gh.mkdir(parents=True, exist_ok=True)
    db_path = index_db()
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")

    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_test USING fts5(x)")
        db.execute("DROP TABLE _fts5_test")
    except sqlite3.OperationalError as e:
        print(f"ERROR: SQLite FTS5 not available: {e}", file=sys.stderr)
        db.close()
        return 1

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

    _ensure_schema(db, sample_dim, use_vec)

    if args.full:
        db.execute("DELETE FROM chunks_fts")
        db.execute("DELETE FROM chunks")
        if use_vec:
            db.execute("DELETE FROM chunks_vec")
        db.commit()

    sources = _collect_sources()

    indexed_files = 0
    indexed_chunks = 0
    embed_calls = 0
    for ws, f in sources:
        rel = str(f.relative_to(gh))
        mtime = f.stat().st_mtime
        cur = db.execute("SELECT mtime FROM chunks WHERE path=? LIMIT 1", (rel,))
        row = cur.fetchone()
        if row and abs(row[0] - mtime) < 1e-6 and not args.full:
            continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
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

    slug_count = _index_slugs(db, sources, args.full)
    db.commit()
    db.close()

    print(f"indexed: {indexed_files} files, {indexed_chunks} chunks at ~/.gowth-mem/index.db")
    print(f"slugs: {slug_count} rows across {len({ws for ws, _ in sources})} sources")
    if use_vec:
        print(f"vector: {embed_calls} embeddings via {provider_info[0]} (dim={sample_dim})")
    else:
        reason = []
        if not HAS_VEC:
            reason.append("sqlite-vec not installed")
        if not HAS_EMBED_MODULE or not (HAS_EMBED_MODULE and detect_provider()):
            reason.append("no embedding API key")
        print(f"vector: skipped ({'; '.join(reason) if reason else 'unknown'}) — FTS5-only index")
    return 0


if __name__ == "__main__":
    sys.exit(main())
