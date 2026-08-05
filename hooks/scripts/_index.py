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
import subprocess
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
from _debug import log_debug  # type: ignore
from _lock import file_lock  # type: ignore
from _tags import TAG_TOKEN_RE, strip_tags_text  # type: ignore  # v4.0 keywords + tag-stable hash

CHUNK_SIZE = 1500

# v4.3: also tolerate a leading list bullet (`- [exp] …`), which is the dominant
# on-disk entry form, matching _tags.TYPE_PREFIX_RE's `[-*]` allowance.
TAG_RE = re.compile(r"^(?:[-*]\s*)?(?:#{2,6}\s*)?\[([a-z-]+)\]\s*")

# YAML frontmatter delimited by --- at the very start of a file.
FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*\r?\n?", re.DOTALL)
KNOWN_TAGS = {"decision", "exp", "ref", "tool", "reflection", "skill-ref",
              "secret-ref", "goal", "hypothesis"}  # v3.9: +goal (intent) +hypothesis (unverified)


def _extract_tag(content: str) -> str:
    """Return the leading [tag] marker value, or '' if absent/unknown."""
    m = TAG_RE.match(content.lstrip())
    if not m:
        return ""
    tag = m.group(1)
    return tag if tag in KNOWN_TAGS else ""


def _frontmatter_tags(text: str) -> list[str]:
    """Parse frontmatter `tags:` (inline `[a, b]` and block `- a` forms)."""
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    lines = text[3:end].split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^tags:\s*(.*)$", line)
        if not m:
            continue
        val = m.group(1).strip()
        if val.startswith("[") and val.endswith("]"):
            return [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        if val:
            return [val.strip("'\"")]
        out: list[str] = []
        for l2 in lines[i + 1:]:
            mm = re.match(r"^\s*-\s+(.+?)\s*$", l2)
            if mm:
                out.append(mm.group(1).strip().strip("'\""))
            else:
                break
        return out
    return []


def _chunk_keywords(content: str, file_text: str | None = None) -> str:
    """Space-joined keyword string for a chunk: all inline `#tag` tokens (no `#`);
    for the file's FIRST chunk (file_text supplied) also its frontmatter tags."""
    kws: list[str] = [m.group(0)[1:].lower() for m in TAG_TOKEN_RE.finditer(content)]
    if file_text is not None:
        kws.extend(t.lower() for t in _frontmatter_tags(file_text))
    seen: set[str] = set()
    out: list[str] = []
    for k in kws:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return " ".join(out)


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


ARCHIVE_DIR = ".archive"
_ARCH_EPOCH_RE = re.compile(r"-\d{9,}$")   # _forget.py appends -<epoch> when archiving


def _collect_archive_sources() -> list[Path]:
    """Return every gzipped file under `.archive/`.

    `_forget.py` gzip-archives raw journal and aged aspects here. Nothing indexed
    them, so archived memory was unsearchable — recoverable only by hand-gunzipping
    or reading git history. Forgetting is meant to keep the BOOTSTRAP cheap, not to
    make knowledge unfindable, so archives are indexed (index.db is gitignored,
    machine-local and rebuildable, so this costs zero synced bytes and zero tokens
    until something is actually retrieved) and excluded from recall by default.
    """
    root = gowth_home() / ARCHIVE_DIR
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.gz") if p.is_file())


def read_source_text(path: Path) -> str:
    """Read a source file, transparently decompressing `.gz`.

    Returns "" on any failure — a corrupt archive must never stop the indexer.
    """
    try:
        if path.suffix == ".gz":
            import gzip
            with gzip.open(path, "rt", errors="ignore") as fh:
                return fh.read()
        return path.read_text(errors="ignore")
    except Exception as exc:
        log_debug("index", f"unreadable source {path}: {exc}")
        return ""


def _archive_stems() -> set[str]:
    """Stems of archived files, with `_forget.py`'s `-<epoch>` suffix removed.

    Used to decide whether a vanished live file still has a recoverable copy.
    """
    out: set[str] = set()
    for p in _collect_archive_sources():
        name = p.name[:-3] if p.name.endswith(".gz") else p.name
        out.add(_ARCH_EPOCH_RE.sub("", Path(name).stem))
    return out


def _git_deleted_paths() -> set[str]:
    """Vault-relative paths that git history still holds (deleted in some commit).

    One subprocess call, best-effort: an empty set just means "cannot prove
    recoverability", which makes the sweep MORE conservative, never less.
    """
    try:
        r = subprocess.run(
            ["git", "log", "--diff-filter=D", "--name-only", "--format="],
            cwd=str(gowth_home()), capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return set()
        return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    except Exception:
        return set()


def sweep_orphans(apply: bool = False, force: bool = False) -> dict:
    """Remove index rows whose file no longer exists on disk.

    27% of the live index (4,031 rows across 237 paths) pointed at files
    `_forget.py` had archived away, so recall cited files the user could not open
    and the dead rows skewed BM25 statistics.

    Data safety first (priority 1): each orphan is classified by whether its content
    survives elsewhere — an archived `.gz` copy, or git history. Rows with NO other
    copy are KEPT unless `force=True`, because for those index.db is the last
    searchable copy in existence. Dry-run is the default.

    Returns a summary dict; never raises.
    """
    summary = {"orphan_paths": 0, "orphan_rows": 0, "recoverable": 0,
               "unrecoverable": 0, "deleted_paths": 0, "deleted_rows": 0,
               "unrecoverable_paths": [], "applied": bool(apply)}
    try:
        db_path = index_db()
        if not db_path.is_file():
            return summary
        gh = gowth_home()
        db = sqlite3.connect(str(db_path))
        try:
            db.execute("PRAGMA busy_timeout=5000")
            rows = db.execute(
                "SELECT path, count(*) FROM chunks GROUP BY path").fetchall()
            orphans = [(p, n) for p, n in rows
                       if not p.startswith(ARCHIVE_DIR + "/") and not (gh / p).exists()]
            summary["orphan_paths"] = len(orphans)
            summary["orphan_rows"] = sum(n for _, n in orphans)
            if not orphans:
                return summary

            stems = _archive_stems()
            git_deleted = _git_deleted_paths()
            deletable: list[tuple[str, int]] = []
            for p, n in orphans:
                recoverable = (_ARCH_EPOCH_RE.sub("", Path(p).stem) in stems
                               or p in git_deleted)
                if recoverable:
                    summary["recoverable"] += 1
                    deletable.append((p, n))
                else:
                    summary["unrecoverable"] += 1
                    summary["unrecoverable_paths"].append(p)
                    if force:
                        deletable.append((p, n))

            if not apply:
                return summary

            try:
                lock_cm = file_lock("index-write", timeout=10.0)
            except Exception:
                lock_cm = None

            def _work() -> None:
                for p, n in deletable:
                    _drop_path_rows(db, p, False)
                    summary["deleted_paths"] += 1
                    summary["deleted_rows"] += n
                db.commit()

            if lock_cm is not None:
                with lock_cm:
                    _work()
            else:
                _work()
            return summary
        finally:
            db.close()
    except Exception as exc:
        log_debug("index", f"sweep_orphans failed: {exc}")
        return summary


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

    # v4.3 self-healing backfill: block-form entries (`## [decision] Title`) keep
    # their marker in `heading`, not `content`, so the content-only backfill above
    # left them untagged. Runs on every migration pass and is idempotent (only
    # touches rows still at tag='').
    db.execute("""
        UPDATE chunks SET tag = LOWER(SUBSTR(LTRIM(heading), 2,
                                             INSTR(LTRIM(heading), ']') - 2))
        WHERE tag = ''
          AND heading IS NOT NULL
          AND SUBSTR(LTRIM(heading), 1, 1) = '['
          AND INSTR(LTRIM(heading), ']') > 2
    """)
    known = "','".join(KNOWN_TAGS)
    db.execute(f"UPDATE chunks SET tag = '' WHERE tag NOT IN ('{known}') AND tag <> ''")
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


def _migrate_keywords_column(db: sqlite3.Connection) -> None:
    """Idempotent v4.0 migration: add `keywords TEXT` to chunks, backfill from
    inline #tags, rebuild chunks_fts to include the keywords column.

    Mirrors `_migrate_tag_column` — wrapped in `file_lock("index-migrate")` so
    two indexers can't race the ALTER/UPDATE/DROP-CREATE-INSERT sequence.
    """
    try:
        lock_cm = file_lock("index-migrate", timeout=10.0)
    except Exception:
        lock_cm = None
    if lock_cm is not None:
        with lock_cm:
            _migrate_keywords_column_inner(db)
    else:
        _migrate_keywords_column_inner(db)


def _migrate_keywords_column_inner(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
    if "keywords" not in cols:
        db.execute("ALTER TABLE chunks ADD COLUMN keywords TEXT NOT NULL DEFAULT ''")
        # Backfill from existing chunk content (inline #tags only; frontmatter
        # tags are re-derived on the next per-file reindex).
        for cid, content in db.execute("SELECT id, content FROM chunks").fetchall():
            kw = _chunk_keywords(content or "")
            if kw:
                db.execute("UPDATE chunks SET keywords=? WHERE id=?", (kw, cid))
        db.commit()

    # Rebuild chunks_fts to include keywords column if it doesn't have it.
    fts_cols = set()
    try:
        fts_info = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='chunks_fts' AND type='table'"
        ).fetchone()
        if fts_info and fts_info[0]:
            fts_cols = set(re.findall(r"\b(\w+)\b", fts_info[0]))
    except Exception:
        pass
    if "keywords" not in fts_cols:
        try:
            db.execute("DROP TABLE IF EXISTS chunks_fts")
        except Exception:
            pass
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
            "tag, keywords, content, content='chunks', content_rowid='id', tokenize='unicode61')"
        )
        db.execute(
            "INSERT INTO chunks_fts(rowid, tag, keywords, content) "
            "SELECT id, tag, keywords, content FROM chunks"
        )
        db.commit()


def _migrate_heading_column(db: sqlite3.Connection) -> None:
    """Idempotent v4.3 migration: index `heading` in chunks_fts.

    chunks_fts was (tag, keywords, content) — the heading was stored in `chunks`
    but never indexed, so the `[type] Title` line of every curated entry and the
    `turn N — HH:MM` anchor of every session log were UNSEARCHABLE. A query for a
    word that appears only in a title returned nothing.

    Mirrors `_migrate_tag_column` / `_migrate_keywords_column`: wrapped in
    `file_lock("index-migrate")` and self-refilling from `chunks` (never delegating
    the refill to a command the user must remember to run).
    """
    try:
        lock_cm = file_lock("index-migrate", timeout=10.0)
    except Exception:
        lock_cm = None
    if lock_cm is not None:
        with lock_cm:
            _migrate_heading_column_inner(db)
    else:
        _migrate_heading_column_inner(db)


def _migrate_heading_column_inner(db: sqlite3.Connection) -> None:
    if "heading" in _fts_columns(db):
        return
    try:
        db.execute("DROP TABLE IF EXISTS chunks_fts")
    except Exception:
        pass
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
        "tag, keywords, heading, content, content='chunks', content_rowid='id', "
        "tokenize='unicode61')"
    )
    db.execute(
        "INSERT INTO chunks_fts(rowid, tag, keywords, heading, content) "
        "SELECT id, tag, keywords, COALESCE(heading, ''), content FROM chunks"
    )
    db.commit()


def _fts_columns(db: sqlite3.Connection) -> set[str]:
    """Return the column names declared on the chunks_fts virtual table."""
    try:
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='chunks_fts' AND type='table'"
        ).fetchone()
    except Exception:
        return set()
    if not row or not row[0]:
        return set()
    return set(re.findall(r"\b(\w+)\b", row[0]))


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
        tag TEXT NOT NULL DEFAULT '',
        keywords TEXT NOT NULL DEFAULT ''
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
        "tag, keywords, heading, content, content='chunks', content_rowid='id', "
        "tokenize='unicode61')"
    )
    # Run migrations in case DB was created by older code without tag/keywords/heading.
    _migrate_tag_column(db)
    _migrate_keywords_column(db)
    _migrate_heading_column(db)
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
    ap.add_argument("--sweep", action="store_true",
                    help="remove index rows whose file no longer exists (dry-run "
                         "unless --apply)")
    ap.add_argument("--apply", action="store_true", help="with --sweep: actually delete")
    ap.add_argument("--force", action="store_true",
                    help="with --sweep --apply: also delete orphans whose content has "
                         "no archived copy and no git history (index.db is their last "
                         "searchable copy)")
    ap.add_argument("--no-archive", action="store_true",
                    help="skip indexing .archive/**.gz")
    args = ap.parse_args()

    if args.sweep:
        s = sweep_orphans(apply=args.apply, force=args.force)
        print(f"orphan paths: {s['orphan_paths']}  rows: {s['orphan_rows']}")
        print(f"  recoverable elsewhere (archive copy or git history): {s['recoverable']}")
        print(f"  NO other copy — index.db is the last one:            {s['unrecoverable']}")
        for p in s["unrecoverable_paths"][:10]:
            print(f"      {p}")
        if s["applied"]:
            print(f"deleted: {s['deleted_paths']} paths, {s['deleted_rows']} rows")
        else:
            print("dry-run — nothing deleted. Re-run with --apply (add --force to "
                  "delete unrecoverable rows too).")
        return 0

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
        text = read_source_text(f)
        if not text.strip():
            continue
        n, embedded = _index_one(db, rel, text, mtime, use_vec)
        indexed_chunks += n
        embed_calls += embedded
        indexed_files += 1

    archived_files = 0
    archived_chunks = 0
    if not args.no_archive:
        for gz in _collect_archive_sources():
            rel = str(gz.relative_to(gh))
            mtime = gz.stat().st_mtime
            row = db.execute("SELECT mtime FROM chunks WHERE path=? LIMIT 1",
                             (rel,)).fetchone()
            if row and abs(row[0] - mtime) < 1e-6 and not args.full:
                continue
            text = read_source_text(gz)
            if not text.strip():
                continue
            n, _ = _index_one(db, rel, text, mtime, False)
            archived_chunks += n
            archived_files += 1

    slug_count = _index_slugs(db, sources, args.full)
    db.commit()
    db.close()

    print(f"indexed: {indexed_files} files, {indexed_chunks} chunks at ~/.gowth-mem/index.db")
    print(f"archive: {archived_files} files, {archived_chunks} chunks "
          f"(searchable via /mem-recall --archive; excluded from normal recall)")
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


def _drop_path_rows(db: sqlite3.Connection, rel: str, use_vec: bool) -> None:
    """Delete every row for *rel*, FTS FIRST.

    chunks_fts is an FTS5 external-content table (content='chunks'), so its rowids
    must go before the chunks rows they mirror — deleting the content row first
    leaves the FTS index pointing at rows that no longer exist.
    """
    old_ids = [r[0] for r in db.execute("SELECT id FROM chunks WHERE path=?", (rel,))]
    for oid in old_ids:
        db.execute("DELETE FROM chunks_fts WHERE rowid=?", (oid,))
        if use_vec:
            db.execute("DELETE FROM chunks_vec WHERE id=?", (oid,))
    db.execute("DELETE FROM chunks WHERE path=?", (rel,))


def _index_one(
    db: sqlite3.Connection,
    rel: str,
    text: str,
    mtime: float,
    use_vec: bool,
) -> tuple[int, int]:
    """Replace all rows for one file. Returns (chunks_written, embeddings_stored).

    Single indexing path shared by `main()` (full/incremental sweep) and
    `reindex_paths()` (write-time refresh), so the two can never drift.
    """
    _drop_path_rows(db, rel, use_vec)
    written = 0
    embedded = 0
    # Chunk the BODY, not the frontmatter. A routed write produces
    # `frontmatter + [type] entry` with no `##` heading, so the file was one chunk
    # whose content began with `---` — the [type] marker was never at content start
    # and every such entry landed untagged (635 live chunks, 81 with a recoverable
    # marker). Frontmatter `tags:` are unaffected: `_chunk_keywords` still receives
    # the ORIGINAL text and harvests them into the keywords column.
    body = FRONTMATTER_RE.sub("", text, count=1)
    for ci, (heading, content) in enumerate(split_chunks(body)):
        # v4.0: hash TAG-STRIPPED content so dedup (is_duplicate) matches an
        # entry whether or not it carries inline #tags.
        h = hashlib.sha1(strip_tags_text(content).encode()).hexdigest()[:16]
        # v4.3: split_chunks() lifts `## [decision] Title` into `heading`, so a
        # content-only probe missed every block-form entry — live vault had 4,714
        # chunks with a `[tag]` heading but only 54 rows with `tag` set, which made
        # the 5.0 BM25 tag weight and the --type filter apply to 0.4% of the corpus.
        tag = _extract_tag(heading) or _extract_tag(content)
        keywords = _chunk_keywords(content, text if ci == 0 else None)
        cid = db.execute(
            "INSERT INTO chunks (path, heading, content, mtime, hash, tag, keywords) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rel, heading, content, mtime, h, tag, keywords),
        ).lastrowid
        db.execute(
            "INSERT INTO chunks_fts(rowid, tag, keywords, heading, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, tag, keywords, heading or "", content),
        )
        if use_vec:
            vec = embed_one(content)
            if vec:
                db.execute(
                    "INSERT INTO chunks_vec(id, embedding) VALUES (?, ?)",
                    (cid, serialize_vec(vec)),
                )
                embedded += 1
        written += 1
    return written, embedded


def reindex_paths(paths) -> int:
    """Refresh the index for specific files. Returns the number of files indexed.

    Called right after a routed write (`_topic.append_entry`, `_lesson.append_lesson`)
    so a just-captured memory is recallable IMMEDIATELY. Before v4.3 nothing on the
    write path touched index.db, so new entries stayed invisible to /mem-recall until
    someone remembered to run /mem-reindex — the live vault's index was 5 days stale.

    Contract, because this runs on the Stop-hook path:
      * never raises — returns 0 on any failure;
      * never CREATES index.db (a missing index must be built by /mem-reindex as a
        whole, not silently half-populated from whichever file was written last);
      * a path whose file no longer exists has its rows dropped;
      * serialised under file_lock("index-write") so concurrent sessions can't
        interleave the delete/insert pair for the same file.
    """
    try:
        gh = gowth_home()
        db_path = index_db()
        if not db_path.is_file():
            return 0
        targets: list[tuple[str, Path]] = []
        for p in paths or []:
            try:
                rel = str(Path(p).resolve().relative_to(gh.resolve()))
            except Exception:
                continue
            targets.append((rel, Path(p)))
        if not targets:
            return 0

        try:
            lock_cm = file_lock("index-write", timeout=5.0)
        except Exception:
            lock_cm = None

        def _work() -> int:
            db = sqlite3.connect(str(db_path))
            try:
                db.execute("PRAGMA busy_timeout=5000")
                done = 0
                for rel, p in targets:
                    if not p.is_file():
                        _drop_path_rows(db, rel, False)
                        continue
                    # read_source_text (not read_text) so archived `.gz` is
                    # decompressed — reading a gzip as text yields mojibake.
                    text = read_source_text(p)
                    if not text.strip():
                        continue
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        continue
                    _index_one(db, rel, text, mtime, False)
                    done += 1
                db.commit()
                return done
            finally:
                db.close()

        if lock_cm is not None:
            with lock_cm:
                return _work()
        return _work()
    except Exception as exc:
        log_debug("index", f"reindex_paths failed: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
