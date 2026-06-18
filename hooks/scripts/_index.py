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
from _lock import file_lock  # type: ignore

CHUNK_SIZE = 1500

TAG_RE = re.compile(r"^(?:#{2,6}\s*)?\[([a-z-]+)\]\s*")  # v3.8: bullet OR `## [type]` block
KNOWN_TAGS = {"decision", "exp", "ref", "tool", "reflection", "skill-ref", "secret-ref"}


def _extract_tag(content: str) -> str:
    """Return the leading [tag] marker value, or '' if absent/unknown."""
    m = TAG_RE.match(content.lstrip())
    if not m:
        return ""
    tag = m.group(1)
    return tag if tag in KNOWN_TAGS else ""


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
    from _home import RESERVED_FILES, RESERVED_SUBDIRS  # type: ignore
    for ws in list_workspaces():
        wd = workspace_dir(ws)
        if not wd.is_dir():
            continue
        # Topic files at workspace root + nested non-reserved subdirs
        for p in wd.rglob("*.md"):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(wd)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] in ("docs", "skills", "research"):
                # docs/skills/research indexed via separate sub paths below
                continue
            if rel.parts and rel.parts[0] == "journal":
                # journal indexed as journal layer
                continue
            out.append((ws, p))
        # Reserved subdirs as separate layers (v3.0: docs/journal/skills/research)
        for sub in ("docs", "journal", "skills", "research"):
            d = wd / sub
            if not d.is_dir():
                continue
            for p in d.rglob("*.md"):
                if p.is_file():
                    out.append((ws, p))
    return out


def _migrate_tag_column(db: sqlite3.Connection) -> None:
    """Idempotent: add `tag TEXT` to chunks if absent, backfill from content,
    rebuild chunks_fts to include tag column.

    Safe to call on an already-migrated DB — all ALTER/DROP/CREATE use IF
    NOT EXISTS / column-existence checks so repeated runs are no-ops.

    v3.4: wrapped in `file_lock("index-migrate")` so two `_index.py` processes
    can't race the ALTER/UPDATE/DROP-CREATE-INSERT sequence. Lock falls open
    on timeout (best-effort serialization; SQLite WAL handles the rest).
    """
    try:
        lock_cm = file_lock("index-migrate", timeout=10.0)
    except Exception:
        lock_cm = None
    if lock_cm is not None:
        with lock_cm:
            _migrate_tag_column_inner(db)
    else:
        _migrate_tag_column_inner(db)


def _migrate_tag_column_inner(db: sqlite3.Connection) -> None:
    """See `_migrate_tag_column`. Body extracted so the lock wrapper stays thin."""
    # Check whether 'tag' column already exists.
    cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
    if "tag" not in cols:
        db.execute("ALTER TABLE chunks ADD COLUMN tag TEXT NOT NULL DEFAULT ''")
        # Backfill existing rows from their content.
        db.execute("""
            UPDATE chunks SET tag = (
                CASE
                    WHEN SUBSTR(LTRIM(content), 1, 1) = '['
                        AND INSTR(LTRIM(content), ']') > 1
                    THEN
                        LOWER(SUBSTR(
                            LTRIM(content),
                            2,
                            INSTR(LTRIM(content), ']') - 2
                        ))
                    ELSE ''
                END
            )
        """)
        # Nullify tag values that are not in KNOWN_TAGS (store as '').
        known = "','".join(KNOWN_TAGS)
        db.execute(f"UPDATE chunks SET tag = '' WHERE tag NOT IN ('{known}')")
        db.commit()

    # Create tag index if absent (safe after column is guaranteed to exist).
    db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_tag ON chunks(tag)")
    db.commit()

    # Rebuild chunks_fts to include tag column if it doesn't have it.
    # Detect by querying the fts5 table schema.
    fts_cols = set()
    try:
        # FTS5 shadow table: chunks_fts_content holds the indexed columns.
        fts_info = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='chunks_fts' AND type='table'"
        ).fetchone()
        if fts_info and fts_info[0]:
            fts_cols = set(re.findall(r"\b(\w+)\b", fts_info[0]))
    except Exception:
        pass

    needs_fts_rebuild = "tag" not in fts_cols
    if needs_fts_rebuild:
        # Drop old FTS table and recreate with both tag + content columns.
        try:
            db.execute("DROP TABLE IF EXISTS chunks_fts")
        except Exception:
            pass
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
            "tag, content, content='chunks', content_rowid='id', tokenize='unicode61')"
        )
        # Repopulate FTS from chunks table.
        db.execute(
            "INSERT INTO chunks_fts(rowid, tag, content) "
            "SELECT id, tag, content FROM chunks"
        )
        db.commit()


def _ensure_schema(db: sqlite3.Connection, sample_dim: int, use_vec: bool) -> None:
    # NOTE: idx_chunks_tag is NOT created here because the old `chunks` table may
    # already exist without the `tag` column. _migrate_tag_column() adds the column
    # first, then creates the index idempotently. This keeps _ensure_schema safe to
    # call on both fresh DBs and pre-v3.4 DBs.
    db.executescript("""
    CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL,
        heading TEXT,
        content TEXT NOT NULL,
        mtime REAL NOT NULL,
        hash TEXT NOT NULL,
        tag TEXT NOT NULL DEFAULT ''
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
        "tag, content, content='chunks', content_rowid='id', tokenize='unicode61')"
    )
    # Run migration in case DB was created by older code without tag column.
    _migrate_tag_column(db)
    if use_vec:
        sqlite_vec.load(db)  # type: ignore
        db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0("
            f"id INTEGER PRIMARY KEY, embedding FLOAT[{sample_dim}])"
        )


def _index_slugs(db: sqlite3.Connection, sources: list[tuple[str, Path]], full: bool) -> int:
    """Refresh `slugs` from frontmatter scan (v3.0). Returns count written.

    PK is `(workspace, slug)`. For v3 topic folders, the landing is
    `<folder>/00-README.md` (which carries the topic frontmatter). For v2.4
    folder-notes the landing is `<folder>/<folder>.md`. Dated aspect files
    (`YYYY-MM-DD-<aspect>.md`) and v2.4 sub-aspect files don't get their own
    slug row — they're recall-able via FTS5/vec but the canonical slug points
    to the parent topic folder's landing (handled by `slug_for_path`).
    """
    if full:
        db.execute("DELETE FROM slugs")
    written = 0
    seen: set[tuple[str, str]] = set()
    for ws, path in sources:
        # Skip MOC files (workspace/registry MOCs and topic READMEs handled below
        # via slug_for_path) and registries.
        if path.name in {"_MAP.md", "_index.md", "files.md", "secrets.md", "tools.md"}:
            continue
        # Skip per-folder lessons.md ledgers — they share the name across topic
        # folders and would collide on PK (workspace, slug). Lessons remain
        # FTS5-searchable via chunks_fts; only the slugs table excludes them.
        if path.name == "lessons.md":
            continue
        # v3.0: skip dated aspect files (YYYY-MM-DD-<aspect>.md) from slugs —
        # they share the parent folder's slug. Recall finds them via FTS5/vec.
        from _home import is_dated_aspect_filename  # type: ignore
        if is_dated_aspect_filename(path.name):
            continue
        # Only frontmatter'd topic landings contribute to slugs.
        fm, _ = parse_file(path)
        slug = fm.get("slug")
        if not slug:
            # v3.0/v2.4 fall back to derived slug (parent folder name for landings).
            from _home import slug_for_path, workspace_dir  # type: ignore
            try:
                ws_root = workspace_dir(ws).resolve()
                slug = slug_for_path(path, ws_root)
            except Exception:
                continue
            if not slug:
                continue
        rel = str(path.relative_to(gowth_home()))
        title = str(fm.get("title") or "")
        status = str(fm.get("status") or "")
        last = str(fm.get("last_touched") or "")
        parents = fm.get("parents") or []
        aliases = fm.get("aliases") or []
        parents_s = ",".join(parents) if isinstance(parents, list) else str(parents)
        # P0-4: wrap aliases with sentinel commas so LIKE '%,slug,%' is exact-token match.
        if isinstance(aliases, list) and aliases:
            aliases_s = "," + ",".join(aliases) + ","
        elif isinstance(aliases, str) and aliases:
            aliases_s = "," + aliases + ","
        else:
            aliases_s = ""
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
            tag = _extract_tag(content)
            cid = db.execute(
                "INSERT INTO chunks (path, heading, content, mtime, hash, tag) VALUES (?, ?, ?, ?, ?, ?)",
                (rel, heading, content, mtime, h, tag),
            ).lastrowid
            db.execute("INSERT INTO chunks_fts(rowid, tag, content) VALUES (?, ?, ?)", (cid, tag, content))
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
