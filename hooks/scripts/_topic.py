"""Topic router (v3.0): topic = FOLDER named <slug>; files inside are dated aspects.

v3 layout — `~/.gowth-mem/workspaces/<ws>/<slug>/`:
  - `00-README.md`                     MOC (TL;DR + auto-index of aspects + manual cross-links)
  - `YYYY-MM-DD-<aspect>.md`           dated aspect file (one context per day)
  - `lessons.md`                       per-folder evergreen ledger (5-field schema)

Reserved at workspace root (NEVER topic): docs, journal, skills, research.
Reserved at workspace root files: _MAP.md, AGENTS.md, workspace.json.
Reserved INSIDE a topic folder: 00-README.md, lessons.md, _MAP.md (forbidden as
aspect slug; rejected by ASPECT_SLUG_RE / blocklist).

Wikilink `[[slug]]` resolves to `<ws>/<slug>/00-README.md` (legacy `<slug>/<slug>.md`
and flat `<slug>.md` still recognised for partial-migration state — see `_wikilink.py`).

`route()` algorithm (v3 §2.2):
  1. Active workspace.
  2. Side-channels: `[secret-ref]` → shared/secrets.md; `[skill-ref]` → workspaces/<ws>/skills/<slug>.md.
  3. Best topic-folder match by keyword overlap (Jaccard) ≥ min_overlap.
  4. New topic → folder slug from top-2 distinctive keywords.
  5. ensure_topic_folder() — F3 fix: always idempotent mkdir + skeleton README.
  6. Aspect slug from top 3-5 distinctive keywords (kebab-case, ≤60).
  7. Target = `<folder>/YYYY-MM-DD-<aspect>.md` (existing today's file → append).
  8. `00-README.md` is NEVER the route target — rebuilt separately by `_moc.rebuild_topic_readme`.

`resolve_topic_folder(slug, ws)` (F4): folder-only resolver for callers (lessons,
reflections, evergreen ledger) that need the folder Path WITHOUT creating a
dated aspect file. Shares `ensure_topic_folder` with `route()` so the folder
exists when callers append to `lessons.md`.

Section mapping (from line prefix):
  [exp]/[reflection] → "## [exp]"
  [ref]/[tool]       → "## [ref]"
  [decision]         → "## [decision]"
  [skill-ref]        → side-channel, no body section
  [secret-ref]       → caller routes to shared/secrets.md
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import safe_write  # type: ignore
from _dedup import _extract_tag, is_duplicate  # type: ignore
from _frontmatter import parse_file  # type: ignore
from _tags import (  # type: ignore
    apply_inline_tags,
    extract_tags,
    max_frontmatter,
    max_per_entry,
    merge_frontmatter_tags,
    tags_enabled,
)
from _home import (  # type: ignore
    RESERVED_FILES,
    RESERVED_SUBDIRS,
    RESERVED_TOPIC_FILES,
    TOPIC_README,
    active_workspace,
    is_dated_aspect_filename,
    is_reserved,
    is_topic_folder,
    iter_topic_files,
    iter_topic_landings,
    read_settings,
    shared_dir,
    skills_dir,
    slug_for_path,
    topic_landing,
    topic_readme,
    topics_dir,
    workspace_dir,
)

STOPWORDS = {
    "this", "that", "with", "from", "have", "been", "were", "they", "them",
    "their", "there", "would", "could", "should", "about", "which", "what",
    "when", "where", "while", "into", "than", "then", "some", "such", "very",
    "just", "only", "your", "you", "for", "the", "and", "but", "not", "all",
    "any", "are", "was", "has", "had", "its", "out", "via", "use", "uses",
    "used", "using",
}

SECTION_FOR_PREFIX = {
    "exp": "## [exp]",
    "reflection": "## [exp]",  # reflections live in exp section per AGENTS.md
    "ref": "## [ref]",
    "tool": "## [ref]",
    "decision": "## [decision]",
}

# v3.0: topic folder slug (unique per workspace)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")

# v3.0: aspect slug (the <aspect> portion of `YYYY-MM-DD-<aspect>.md`)
ASPECT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")

# Inside-topic-folder names that cannot be used as an aspect slug.
ASPECT_BLOCKLIST = frozenset({"readme", "lessons", "00-readme"})

# v4.0 data-value guard: a NEW topic slug matching any of these is junk (secret
# placeholders, redaction markers, throwaway names) — route to the default topic
# (misc) instead of minting a parasitic topic. Fixes the live vault failure where
# `AKIAIOSFODNN7EXAMPLE` minted an `akia...` topic.
_DENY_NEW_SLUG_RE = re.compile(
    r"(example|placeholder|redacted|akia[a-z0-9]+|xxx+|^test-?\d*$|^todo$)"
)


class UnroutableError(ValueError):
    """The vault offers no safe topic folder for an entry (every `-notes`
    name beside a domain is taken, or the folder resolves outside the
    workspace). `append_entry_status` reports it as `rejected:unroutable`;
    any OTHER ValueError is a bug and must surface, not read as a refusal."""


def _min_overlap(s) -> int:
    """`topic_routing.min_keyword_overlap`, or 3 when it is no integer — a
    hand-edited `"three"` used to raise inside planning on every append."""
    routing = s.get("topic_routing", {}) if isinstance(s, dict) else {}
    v = routing.get("min_keyword_overlap", 3) if isinstance(routing, dict) else 3
    try:
        return int(v)
    except (TypeError, ValueError):
        return 3


def _default_topic(s) -> str:
    """`topic_routing.default_topic`, or `misc` when the setting is no usable
    top-level topic name — hand-edited settings like `Misc`, `docs`,
    `misc notes`, or `../..` (which made planning scan the vault's PARENT
    for topic folders before the gate had even run)."""
    routing = s.get("topic_routing", {}) if isinstance(s, dict) else {}
    t = routing.get("default_topic", "misc") if isinstance(routing, dict) else "misc"
    if isinstance(t, str) and SLUG_RE.match(t) and not is_reserved(t) and not is_reserved(f"{t}.md"):
        return t
    return "misc"


def _guard_new_slug(slug: str, default_topic: str) -> str:
    """Return `default_topic` if `slug` looks like a junk/placeholder topic name
    — or is a reserved workspace name: `ensure_topic_folder` refuses `research`,
    `docs`, …, so "[exp] see the research" crashed the write (v4.7.6)."""
    if _DENY_NEW_SLUG_RE.search(slug) or is_reserved(slug) or is_reserved(f"{slug}.md"):
        return default_topic
    return slug


# v4.0: workspace name guard. Public entrypoints that take `ws` and cause a
# filesystem effect (append_entry / append_lesson) must reject arbitrary text
# BEFORE any mkdir — a swapped-args call once mkdir'd a junk directory named
# after prose, and a >255-char ws raised a raw OSError.
WS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def validate_workspace(ws: str) -> str:
    """Raise ValueError if `ws` is not a well-formed workspace name."""
    if not isinstance(ws, str) or not WS_RE.match(ws):
        raise ValueError(f"invalid workspace: {ws!r} (must match {WS_RE.pattern})")
    return ws


def _validate_slug(slug: str) -> str:
    """Reject path-traversal and non-conforming topic slugs."""
    if not SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug: {slug!r} (must match {SLUG_RE.pattern})"
        )
    return slug


def _validate_aspect_slug(slug: str) -> str:
    """Reject invalid aspect slugs (must match ASPECT_SLUG_RE, not in blocklist,
    not leading `_`, not pure digits — digits would collide with the date prefix)."""
    if not slug:
        raise ValueError("empty aspect slug")
    if slug in ASPECT_BLOCKLIST:
        raise ValueError(f"aspect slug {slug!r} is reserved (00-README/lessons)")
    if slug.startswith("_"):
        raise ValueError(f"aspect slug {slug!r} cannot start with '_'")
    if slug.isdigit():
        raise ValueError(f"aspect slug {slug!r} cannot be pure digits (date prefix collision)")
    if not ASPECT_SLUG_RE.match(slug):
        raise ValueError(
            f"invalid aspect slug: {slug!r} (must match {ASPECT_SLUG_RE.pattern})"
        )
    return slug


def _keywords_in_order(text: str, min_len: int = 4) -> list[str]:
    """Distinct keywords in order of first appearance."""
    seen: set[str] = set()
    out: list[str] = []
    for w in re.findall(rf"\b\w{{{min_len},}}\b", text.lower()):
        if w not in STOPWORDS and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _extract_keywords(text: str, min_len: int = 4) -> set[str]:
    return set(_keywords_in_order(text, min_len))


def _slugify(words: list[str], max_len: int = 60) -> str:
    s = "-".join(words)
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len] or "misc"


def _ranked(words: list[str], n: int) -> list[str]:
    """Top-`n` distinctive keywords: longest first; equal lengths keep their
    order of first appearance in the text (a stable sort).

    v4.7.6: `sorted(<set>, key=len)` kept the set's iteration order among
    equal-length words, and str hashing is randomised per process — the same
    entry minted a different topic folder or aspect filename on every run
    (three runs, three slugs). An alphabetical tie-break was deterministic but
    put the gate-mandated `because` into slugs systematically (`because-cooling`);
    first appearance favours the subject, which leads the sentence.
    """
    return sorted(words, key=len, reverse=True)[:n]


def derive_aspect_slug(content: str, max_words: int = 5, max_len: int = 60) -> str:
    """v3.0: derive `<aspect>` from top 3-5 distinctive keywords of an entry.

    Returns a sanitized kebab-case slug ≤max_len chars. Falls back to 'note'
    when keyword extraction yields no usable slug, or when the candidate
    collides with the blocklist (00-README / lessons) / leading `_` / pure digits.
    """
    words = _keywords_in_order(content)
    if not words:
        return "note"
    ranked = _ranked(words, max_words)
    candidate = _slugify(ranked, max_len=max_len)
    if not candidate or candidate in ASPECT_BLOCKLIST or candidate.startswith("_") or candidate.isdigit():
        return "note"
    if not ASPECT_SLUG_RE.match(candidate):
        return "note"
    return candidate


def _walk_topics(ws: str | None) -> list[Path]:
    """v3.0: walks workspace root, skipping reserved subdirs + reserved files."""
    return iter_topic_files(ws)


def detect_section(line: str) -> str | None:
    """Match `[exp]`/`[ref]`/`[decision]`/... at start (after optional `- ` bullet)."""
    m = re.match(r"^\s*[-*]?\s*\[(?P<tag>[a-z-]+)\]", line)
    if not m:
        return None
    tag = m.group("tag")
    return SECTION_FOR_PREFIX.get(tag)


def _detect_line_type(line: str) -> str | None:
    """Return the raw line-type tag (`exp`/`ref`/`decision`/`tool`/`reflection`/
    `skill-ref`/`secret-ref`) or None. Used for side-channel routing."""
    m = re.match(r"^\s*[-*]?\s*\[(?P<tag>[a-z-]+)\]", line)
    if not m:
        return None
    return m.group("tag")


def _today_aspect_path(folder: Path, aspect_slug: str, today: str | None = None) -> Path:
    """v3.0: build `<folder>/YYYY-MM-DD-<aspect>.md` for today (or supplied date)."""
    today = today or date.today().isoformat()
    return folder / f"{today}-{aspect_slug}.md"


def ensure_topic_folder(slug: str, ws: str | None = None,
                        title: str | None = None,
                        parents: list[str] | None = None,
                        topic_type: str = "misc",
                        summary: str = "") -> Path:
    """v3.0 (F3 fix): idempotent — mkdir + write empty `00-README.md` if missing.

    Returns the topic FOLDER path (not the README path). Existing folder is
    untouched; existing README is preserved verbatim. Without this idempotent
    ensure, writes to `<slug>/<date>-<aspect>.md` silently fail when the folder
    doesn't exist on the local machine (e.g. multi-machine sync edge case).
    """
    from _topic_templates import render as _render_readme  # type: ignore

    _validate_slug(slug)
    if is_reserved(slug) or is_reserved(f"{slug}.md"):
        raise ValueError(
            f"slug {slug!r} collides with reserved name "
            f"(docs/journal/skills/research/_MAP/AGENTS/workspace.json)"
        )

    ws = ws or active_workspace()
    parents = parents or []
    for parent in parents:
        if not SLUG_RE.match(parent):
            raise ValueError(f"invalid parent segment: {parent!r}")
        if parent in RESERVED_SUBDIRS:
            raise ValueError(f"parent {parent!r} is a reserved subdir")

    base = workspace_dir(ws).resolve()
    folder = base
    for parent in parents:
        folder = folder / parent
    folder = folder / slug
    folder = folder.resolve() if folder.exists() else folder
    folder.mkdir(parents=True, exist_ok=True)

    # Path-escape guard (resolve AFTER mkdir so symlinks are caught)
    resolved = folder.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"resolved path {resolved} escapes workspace root {base}") from exc

    readme = resolved / TOPIC_README
    if not readme.is_file():
        today = date.today().isoformat()
        nice_title = title or slug.replace("-", " ").title()
        body = _render_readme(topic_type, slug, nice_title, today, parents, summary)
        safe_write(readme, body)

    return resolved


def resolve_topic_folder(slug: str, ws: str | None = None, *, ensure: bool = True) -> Path:
    """v3.0 (F4 fix): folder-only resolver for lessons / reflections / evergreen.

    Returns the topic FOLDER path. Idempotently ensures the folder exists
    (via `ensure_topic_folder`) but does NOT create any dated aspect file.
    Use this when the caller writes to `<folder>/lessons.md` directly.

    v4.7.6: a topic that exists only NESTED (`<ws>/<domain>/<slug>/`) resolves
    to that folder instead of getting a top-level twin; none → `<ws>/<slug>/`,
    as before. A slug naming a DOMAIN, or matching several nested topics,
    raises ValueError naming the candidates (a README would have hidden the
    domain's topics; a third top-level twin helps nobody), as does one that
    resolves outside the workspace through a symlink. `ensure=False` returns
    the same folder without creating anything.
    """
    ws = ws or active_workspace()
    _validate_slug(slug)
    root = workspace_dir(ws)
    top = root / slug
    if top.is_dir():
        if not _inside_workspace(top, ws):
            raise ValueError(f"topic {slug!r} resolves outside the workspace (symlink?)")
        if _is_domain(top):
            inner = sorted(p.parent.name for p in iter_topic_landings(ws)
                           if top in p.parent.parents)
            raise ValueError(f"{slug!r} is a domain folder, not a topic — name one of: "
                             + ", ".join(inner))
        return _ensure_landing(top, ws) if ensure else top
    nested = [p.parent for p in iter_topic_landings(ws) if p.parent.name == slug]
    if any(not _inside_workspace(n, ws) for n in nested):
        raise ValueError(f"topic {slug!r} resolves outside the workspace (symlink?)")
    if len(nested) > 1:
        raise ValueError(f"topic {slug!r} is ambiguous — "
                         + ", ".join(str(n.relative_to(root)) for n in nested))
    if nested:
        return _ensure_landing(nested[0], ws) if ensure else nested[0]
    return ensure_topic_folder(slug, ws=ws) if ensure else top


def _pick_topic(content: str, ws: str,
                settings: dict | None) -> tuple[str, Path | None]:
    """Topic selection shared by `route()`, `derive_topic_slug()` and
    `derive_topic_folder()` — one copy, so the three can no longer drift.

    Returns `(slug, folder)`. `folder` is an EXISTING topic folder to write
    into (best keyword-overlap match, or an existing nested folder named like
    the new slug). `None` means `<ws>/<slug>/` is to be created: no keywords
    (default topic), no match good enough (new slug from the top-2 keywords),
    or a legacy flat `<ws>/<slug>.md` match (promoted to a folder).

    v4.7.6: topic identity is WHERE a file lives (`slug_for_path`), never its
    frontmatter `slug:`. A dated aspect carries `slug: <topic>-<aspect>`
    (`_validate.fix_aspect`), so each time an aspect out-scored its folder's
    README the old code minted a README-only `<topic>-<aspect>/` sibling
    (31 in the live vault) — and `_lesson` filed a lesson inside one.
    """
    s = settings or read_settings()
    min_overlap = _min_overlap(s)
    default_topic = _default_topic(s)

    kws_in_order = _keywords_in_order(content)
    kws = set(kws_in_order)
    if not kws:
        return _avoid_domain(default_topic, workspace_dir(ws)), None

    # The UNRESOLVED dir: `_walk_topics` yields paths under exactly this base.
    # A resolved root never equals `f.parent` under a symlinked home (every
    # macOS temp dir), which misread a flat `<ws>/<name>.md` as a folder file.
    ws_dir = workspace_dir(ws)
    slug_index: dict[str, Path] = {}
    domains: dict[Path, bool] = {}
    best_path: Path | None = None
    best_overlap = 0
    # Sorted: `rglob` order is filesystem-dependent (APFS vs ext4), and ties
    # keep the FIRST best file — so two machines sharing one vault could route
    # the same entry to different topics.
    for f in sorted(_walk_topics(ws)):
        slug = slug_for_path(f, ws_dir)
        if not SLUG_RE.match(slug):
            continue
        if f.parent == ws_dir and (is_dated_aspect_filename(f.name) or is_reserved(slug)):
            # Not a legacy flat topic: a loose root-level aspect is an artifact
            # (the symlinked-home bug wrote some) and would be promoted into a
            # date-named folder; `<ws>/research.md` cannot become a folder.
            continue
        if f.parent != ws_dir:
            dom = domains.get(f.parent)
            if dom is None:
                dom = domains[f.parent] = _is_domain(f.parent)
            if dom:
                # A loose file inside a DOMAIN: "its folder" has no landing, so
                # every entry that matched it would pile up where no MOC, list
                # or bootstrap ever looks.
                continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        slug_index.setdefault(slug, f)
        overlap = len(kws & _extract_keywords(text))
        if overlap > best_overlap:
            best_overlap, best_path = overlap, f

    if best_path is not None and best_overlap >= min_overlap:
        folder = best_path.parent
        if folder != ws_dir:
            return slug_for_path(best_path, ws_dir), folder
        # Legacy flat `<ws>/<stem>.md` → promoted to `<ws>/<stem>/` — unless
        # that name is already a domain's.
        return _avoid_domain(slug_for_path(best_path, ws_dir), ws_dir), None

    new_slug = _avoid_domain(
        _guard_new_slug(_slugify(_ranked(kws_in_order, 2)) or default_topic, default_topic), ws_dir)
    # Already exists (e.g. nested via /mem-ops restructure) → write there, don't shadow.
    existing = slug_index.get(new_slug)
    if existing is not None and existing.parent != ws_dir:
        return new_slug, existing.parent
    return new_slug, None


def _inside_workspace(folder: Path, ws: str) -> bool:
    """True iff `folder` RESOLVES inside the workspace — a symlinked topic
    folder pointing elsewhere does not (a lesson written through it is never
    synced or indexed, while the CLI said "appended")."""
    try:
        folder.resolve().relative_to(workspace_dir(ws).resolve())
        return True
    except (ValueError, OSError):
        return False


def _avoid_domain(slug: str, ws_dir: Path) -> str:
    """A NEW (or promoted) topic must not take a DOMAIN folder's name —
    `ensure_topic_folder` would give the domain a README and hide its topics
    (e.g. a default topic `misc/` that holds topic folders). Use the first of
    `<slug>-notes`, `<slug>-notes-2`, … that is not a domain; the stem is
    shortened so every candidate stays within the 60-char slug limit."""
    if not _is_domain(ws_dir / slug):
        return slug
    for i in range(1, 21):
        suffix = "-notes" if i == 1 else f"-notes-{i}"
        cand = slug[:60 - len(suffix)].rstrip("-") + suffix
        if not _is_domain(ws_dir / cand):
            return cand
    raise UnroutableError(f"no free topic name beside domain {slug!r}")


def _is_domain(folder: Path) -> bool:
    """A folder with no landing of its own that holds topic folders somewhere
    below it. `iter_topic_landings` recurses into such a folder; a README
    there would end the recursion and hide every topic under it."""
    if is_topic_folder(folder):
        return False
    try:
        return any(is_topic_folder(d) for d in folder.rglob("*") if d.is_dir())
    except OSError:
        return False


def _ensure_landing(folder: Path, ws: str) -> Path:
    """F3 for an EXISTING topic folder: add a skeleton README only when the
    folder has no landing at all. The README is placed from the folder's own
    location (parents + name), never re-derived from a slug, so this can only
    ever touch `folder` itself — the old `ensure_topic_folder(best_slug)` built
    `<ws>/<best_slug>/`, a top-level sibling for nested topics and for any
    aspect-derived slug. A legacy `<dir>/<dir>.md` landing counts: a skeleton
    README would shadow it in `topic_landing()`.
    """
    if not _inside_workspace(folder, ws):
        # Never swallowed (unlike the reserved-segment case below): writing
        # through a symlink out of the vault loses the entry silently.
        raise UnroutableError(f"{folder} resolves outside workspace {ws!r} (symlink?)")
    if is_topic_folder(folder) or _is_domain(folder):
        return folder  # already a topic — or a DOMAIN, which a README would turn into one
    try:
        rel = folder.relative_to(workspace_dir(ws))
        ensure_topic_folder(rel.parts[-1], ws=ws, parents=list(rel.parts[:-1]))
    except (ValueError, OSError) as exc:
        # Reserved or non-conforming segment (e.g. an unmigrated `My Notes/`):
        # the folder exists, so the entry is still written — only the README
        # is skipped (the old path raised here and lost the write).
        try:
            from _debug import log_debug  # type: ignore
            log_debug("topic", f"no landing added to {folder}: {exc}")
        except Exception:
            pass
    return folder


def derive_topic_slug(content: str, ws: str | None = None,
                      settings: dict | None = None) -> str:
    """v3.0: return the topic FOLDER slug for `content` without spawning files.

    Same selection as `route()` (`_pick_topic`: existing-topic match by keyword
    overlap, else top-2 distinctive keywords, else default `misc`), but never
    touches the filesystem. Writers should use `derive_topic_folder()`, which
    also resolves NESTED topic folders.
    """
    slug, _folder = _pick_topic(content, ws or active_workspace(), settings)
    return slug


def plan_topic_folder(content: str, ws: str | None = None,
                      settings: dict | None = None) -> tuple[str, Path | None]:
    """The topic choice for `content`, touching nothing: `(slug, folder)` as
    `_pick_topic` returns it. Pair with `materialise_topic_folder` to decide,
    check (dedup / gate), and only then create — with a single vault walk."""
    return _pick_topic(content, ws or active_workspace(), settings)


def materialise_topic_folder(slug: str, folder: Path | None, ws: str | None = None) -> Path:
    """Create what `plan_topic_folder` chose (see `_materialise`)."""
    return _materialise(slug, folder, ws or active_workspace())


def derive_topic_folder(content: str, ws: str | None = None,
                        settings: dict | None = None, *, ensure: bool = True) -> Path:
    """v4.7.6: the topic FOLDER `content` belongs in — ensured, but with no
    dated aspect spawned. Used by `_lesson.py` for `lessons.md`.

    Unlike `resolve_topic_folder(derive_topic_slug(...))` it returns the
    matched folder itself, so a nested topic (`<ws>/<domain>/<slug>/`) never
    gets a top-level `<ws>/<slug>/` twin. `ensure=False` returns the same
    folder without creating anything (for a rejected write).
    """
    ws = ws or active_workspace()
    slug, folder = _pick_topic(content, ws, settings)
    if not ensure:
        return folder if folder is not None else workspace_dir(ws) / slug
    return _materialise(slug, folder, ws)


def _plan(content: str, ws: str, s: dict) -> tuple[str, Path | None, Path | None, str | None]:
    """The routing DECISION — reads the vault, never writes: `(slug, folder,
    side, section_hint)`. `side` is the side-channel file for `[secret-ref]` /
    `[skill-ref]` (nothing is ensured for those); otherwise `folder` is the
    existing topic folder to write into, or None to create `<ws>/<slug>/`.
    Split from the side effects (`_materialise`) so a write can be refused
    before anything is created."""
    default_topic = _default_topic(s)

    first_line = content.splitlines()[0] if content else ""
    line_type = _detect_line_type(first_line)
    section_hint = SECTION_FOR_PREFIX.get(line_type) if line_type else None

    # Side-channel: [secret-ref] → shared/secrets.md
    if line_type == "secret-ref":
        return "secrets", None, shared_dir() / "secrets.md", None

    # Side-channel: [skill-ref] → workspaces/<ws>/skills/<slug>.md
    if line_type == "skill-ref":
        skill_slug = _derive_skill_slug(content) or default_topic
        return skill_slug, None, skills_dir(ws) / f"{skill_slug}.md", None

    slug, folder = _pick_topic(content, ws, s)
    return slug, folder, None, section_hint


def _planned_target(content: str, ws: str, s: dict):
    """`(slug, folder, side, section_hint, target)` — the file an entry would
    be appended to, computed without creating anything."""
    slug, folder, side, hint = _plan(content, ws, s)
    target = side if side is not None else _today_aspect_path(
        folder if folder is not None else workspace_dir(ws) / slug, derive_aspect_slug(content))
    return slug, folder, side, hint, target


def _materialise(slug: str, folder: Path | None, ws: str) -> Path:
    """The side-effect half of routing: an existing folder gets a README only
    if it has no landing; otherwise `<ws>/<slug>/` is created (or a legacy
    flat `<ws>/<slug>.md` topic promoted to a folder)."""
    if folder is not None:
        return _ensure_landing(folder, ws)
    try:
        return ensure_topic_folder(slug, ws=ws)
    except ValueError as exc:   # e.g. `<ws>/<slug>` is a symlink out of the vault
        raise UnroutableError(str(exc)) from exc


def route(content: str, ws: str | None = None,
          settings: dict | None = None) -> tuple[str, Path, str | None]:
    """v3.0: return `(slug, file_path, section_hint)` for a memory entry.

    `slug` = topic folder name. `file_path` = the EXACT file to append to
    (today's dated aspect, NEVER `00-README.md`). `section_hint` is the
    in-file heading (e.g. "## [exp]") or None for caller-decides.

    Side-channels (returned early, no folder ensure):
      `[secret-ref]` → `shared/secrets.md`
      `[skill-ref]`  → `workspaces/<ws>/skills/<slug>.md`

    Otherwise routes to `<ws>/<slug>/<today>-<aspect>.md`.
    """
    s = settings or read_settings()
    ws = ws or active_workspace()
    slug, folder, side, section_hint = _plan(content, ws, s)
    if side is not None:
        return (slug, side, None)
    # No keywords → default topic with a "note" aspect (derive_aspect_slug's fallback).
    folder = _materialise(slug, folder, ws)
    return (slug, _today_aspect_path(folder, derive_aspect_slug(content)), section_hint)


def append_entry(content: str, ws: str | None = None,
                 settings: dict | None = None) -> tuple[Path, bool]:
    """v3.4: route + cross-file dedup + atomic append. Returns (path, written).

    This is the Python write path for routed topic entries. Callers that
    previously used `route()` + raw write should switch to this helper so
    `is_duplicate(ws_root, tag, content)` actually blocks cross-file repeats.

    `written` is False when dedup or the gate refused the entry — see
    `append_entry_status()` for which; nothing is created on disk then. The
    path is still returned so callers can log it. Section heading injection
    is left to the caller (matches existing route() contract).
    """
    path, status = append_entry_status(content, ws=ws, settings=settings)
    return path, status == "written"


def append_entry_status(content: str, ws: str | None = None,
                        settings: dict | None = None) -> tuple[Path, str]:
    """v4.7.6: `append_entry` with the outcome spelled out — `written`,
    `duplicate` or `rejected:<gate rule>` — and decided BEFORE anything is
    created. The old order routed first, so a refused entry still left a
    README-only topic folder behind (one no junk check can prove), and the
    CLI printed `duplicate` for a gate refusal: the memory teammate then
    no-op'd an entry it could have repaired (e.g. by adding its `Source:`).
    """
    ws = ws or active_workspace()
    validate_workspace(ws)  # reject junk ws BEFORE any mkdir
    s = settings if isinstance(settings, dict) else read_settings()
    try:
        slug, folder, side, _section, target = _planned_target(content, ws, s)
    except UnroutableError as exc:   # no routable topic name (e.g. every candidate is a domain)
        from _debug import log_debug  # type: ignore
        log_debug("topic", f"unroutable: {exc}")
        return workspace_dir(ws), "rejected:unroutable"
    tag = _extract_tag(content)
    # Dedup + gate run on the ORIGINAL (untagged) content. is_duplicate hashes
    # tag-stripped content, so the check is stable regardless of inline #tags.
    if tag and is_duplicate(workspace_dir(ws), tag, content):
        return target, "duplicate"
    # v3.6: hard write-rules gate — reject junk before it lands (canon §1).
    # Deterministic, no LLM. Gated by settings.gate.enabled (default true).
    try:
        from _home import setting as _setting  # type: ignore
        if _setting("gate.enabled", bool, True, settings=s):
            from _gate import evaluate as _gate_eval  # type: ignore
            _v = _gate_eval(content)
            if not _v.ok:
                from _debug import log_debug  # type: ignore
                log_debug("topic", f"gate reject [{_v.reason}]: {content[:80]}")
                return target, f"rejected:{_v.reason or 'gate'}"
    except Exception:
        pass  # gate is best-effort; never block a write on gate internals failing

    if side is None:
        try:
            target = _today_aspect_path(_materialise(slug, folder, ws), derive_aspect_slug(content))
        except UnroutableError as exc:   # e.g. the folder resolves outside the workspace
            from _debug import log_debug  # type: ignore
            log_debug("topic", f"unroutable: {exc}")
            return target, "rejected:unroutable"

    # v4.0: deterministic auto-tagging. Append inline #tags to the entry's first
    # line, then union them into the aspect file's frontmatter tags:.
    entry = content
    tags: list[str] = []
    if tags_enabled(s):
        try:
            tags = extract_tags(content, max_per_entry(s))
            entry = apply_inline_tags(content, tags)
        except Exception:
            entry = content  # tagging is best-effort; never block a write

    if target.is_file():
        existing = target.read_text(errors="ignore")
        body = existing.rstrip() + "\n" + entry.rstrip() + "\n"
    else:
        body = entry.rstrip() + "\n"
    safe_write(target, body)

    # Union tags into frontmatter — only for dated aspect files (not secrets.md,
    # skills/<slug>.md, or lessons.md side-channels).
    if tags and is_dated_aspect_filename(target.name):
        try:
            new_text = merge_frontmatter_tags(
                target.read_text(errors="ignore"), tags, max_frontmatter(s)
            )
            safe_write(target, new_text)
        except Exception:
            pass

    # v4.1: new aspects must be born schema-conformant. The routed write used
    # to create tags-only frontmatter (no type/date/topic/slug/title), leaving
    # the file invisible to wikilinks/recall/MOC until a manual
    # `_validate --fix` (13 such files observed in the live vault).
    if is_dated_aspect_filename(target.name):
        try:
            from _validate import fix_aspect  # type: ignore
            fix_aspect(target)
        except Exception:
            pass  # best-effort; never block a write on frontmatter repair

    # v4.3: refresh the index for just this file so the entry is recallable NOW.
    # Nothing on the write path used to touch index.db, so a captured memory stayed
    # invisible to /mem-recall until a manual /mem-ops reindex (live index: 5 days stale).
    try:
        from _index import reindex_paths  # type: ignore
        reindex_paths([target])
    except Exception:
        pass  # best-effort; a stale index must never fail a write

    return target, "written"


def _derive_skill_slug(content: str) -> str | None:
    """Pull `[skill-ref:<slug>]` or fall back to first distinctive keyword."""
    m = re.search(r"\[skill-ref:([a-z0-9][a-z0-9-]{0,59})\]", content)
    if m:
        return m.group(1)
    kws = _keywords_in_order(content)
    if not kws:
        return None
    return _slugify(_ranked(kws, 1)) or None


def ensure_topic(slug: str, ws: str | None = None, title: str | None = None,
                 parents: list[str] | None = None, topic_type: str = "misc",
                 summary: str = "") -> Path:
    """v3.0: Create `workspaces/<ws>/<parents>/<slug>/00-README.md` from skeleton.

    Returns the README path (the canonical landing for the topic). Idempotent —
    existing README preserved. Folder is created if missing. Use this when
    callers need a stable landing path (e.g. /mem-topic --ensure); for routine
    writes use `route()` (dated aspect) or `resolve_topic_folder()` (folder-only).
    """
    folder = ensure_topic_folder(slug, ws=ws, title=title, parents=parents,
                                 topic_type=topic_type, summary=summary)
    return folder / TOPIC_README


def list_topics(ws: str | None = None) -> list[dict]:
    """Return [{slug, title, status, last_touched, parents, path}] sorted by last_touched desc.

    `path` points to the topic's README (canonical landing), not to a specific aspect.
    """
    ws = ws or active_workspace()
    ws_root = workspace_dir(ws).resolve()
    out: list[dict] = []
    seen: set[str] = set()
    for f in iter_topic_landings(ws):
        fm, _ = parse_file(f)
        slug = fm.get("slug") or slug_for_path(f, ws_root)
        if slug in seen:
            continue
        seen.add(slug)
        parents = fm.get("parents") or []
        if not isinstance(parents, list):
            parents = []
        out.append({
            "slug": slug,
            "title": fm.get("title") or slug.replace("-", " ").title(),
            "status": fm.get("status") or "",
            "last_touched": fm.get("last_touched") or "",
            "parents": parents,
            "path": f,
        })
    out.sort(key=lambda d: d.get("last_touched") or "", reverse=True)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--route")
    ap.add_argument("--append", help="route + cross-file dedup + atomic append (v3.4)")
    ap.add_argument("--ws")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--ensure")
    ap.add_argument("--type", default="misc",
                    help="topic type for --ensure (runbook/incident/reference/research/strategy/how-to/concept/decision/tool/misc)")
    ap.add_argument("--title", default=None, help="title for --ensure (default: derived from slug)")
    ap.add_argument("--parents", default="", help="comma-separated parent segments for --ensure")
    ap.add_argument("--summary", default="", help="cốt lõi 1-line summary for --ensure")
    args = ap.parse_args()
    if args.route:
        # A PREVIEW (/mem-topic route): it must not create the folder it
        # predicts — a previewed gate-reject used to leave permanent junk.
        slug, _f, _side, section, path = _planned_target(
            args.route, args.ws or active_workspace(), read_settings())
        print(f"{slug}\t{path}\t{section or ''}")
    elif args.append:
        path, status = append_entry_status(args.append, ws=args.ws)
        print(f"{path}\t{status}")   # written | duplicate | rejected:<gate rule>
    elif args.list:
        for t in list_topics(args.ws):
            print(f"{t['slug']:30s} {t['status']:10s} {t['last_touched']:10s} {t['title']}")
    elif args.ensure:
        parents = [p for p in args.parents.split(",") if p.strip()]
        p = ensure_topic(args.ensure, ws=args.ws, title=args.title,
                         parents=parents, topic_type=args.type, summary=args.summary)
        print(p)
    else:
        ap.print_help()
