#!/usr/bin/env python3
"""Deterministic auto-tagging (v4.0) — stdlib keyword extraction, no LLM.

The write path (`_topic.append_entry` + `_lesson.append_lesson`) calls
`extract_tags()` on each new entry and appends an inline `#tag` suffix to the
entry's first line, then unions the tags into the aspect file's frontmatter
`tags:`. The index (`_index.py`) harvests the `#tags` into a weighted FTS5
`keywords` column so `/mem-recall --keyword` can filter on them.

Everything here is deterministic: the same input text always yields the same
tag list (stable ordering, no randomness, no clock, no LLM). That property is
load-bearing — `_dedup.py` hashes TAG-STRIPPED content so an entry dedupes
identically with or without its inline tags (see `strip_tags` / `strip_tags_text`).

Algorithm (canon `.claude/research/v4.0-metacognition.md` §2):
  1. Strip `[type]` prefix, `**Field:**` bold labels, URLs, code fences, inline code.
  2. Harvest high-value tokens first (order of appearance): `` `code` ``, dotted.paths,
     snake_case, kebab-case, CamelCase, `--flags`, UPPER acronyms. Drop pure
     version/date/hex/number tokens, keep names.
  3. Score remaining prose: lowercase, split on non-word, drop EN+VI stopwords and
     len<3 / pure-digit tokens; score = freq × early-position boost × (len>4 boost).
  4. Normalize each tag (lowercase, `[^a-z0-9._-]`→`-`), collapse prefix near-dupes.
  5. Cap at `max_tags` (settings `tags.max_per_entry`); short entries yield fewer —
     never pad.

CLI:
  python3 _tags.py --extract '[decision] use FTS5 for recall'   # print tags
  python3 _tags.py --backfill [--ws X] [--apply]                # frontmatter union
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import (  # type: ignore
    RESERVED_SUBDIRS,
    gowth_home,
    list_workspaces,
    read_settings,
    workspace_dir,
)
from _lock import file_lock  # type: ignore

# ── public token regex (used by _index.py, _dedup.py) ────────────────────
# A stored/inline tag token: `#` + identifier char, then [a-z0-9._-]. Leading `_`
# is allowed so private-module tags (`#_query.py`) round-trip. Case-insensitive
# on read so a stray uppercase hashtag still matches.
TAG_TOKEN_RE = re.compile(r"#[A-Za-z0-9_][A-Za-z0-9._-]*")

# ── entry-shape helpers ──────────────────────────────────────────────────
TYPE_PREFIX_RE = re.compile(r"^\s*(?:[-*]\s*|#{1,6}\s*)*\[[a-z][a-z-]*\]\s*", re.IGNORECASE)
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
URL_RE = re.compile(r"https?://\S+")
BOLD_LABEL_RE = re.compile(r"\*\*[^*]+?:\*\*")   # **Symptom:** **Root cause:**
# Only markdown emphasis markers `*`/`~` — NOT `_` (underscores are identifiers:
# snake_case, _private, GOWTH_MEM_HOME must survive for priority harvesting).
EMPHASIS_RE = re.compile(r"[*~]{1,3}")

# ── priority (high-value) identifier patterns ────────────────────────────
DOTTED_RE = re.compile(r"[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+")   # _topic.py settings.json
SNAKE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+")   # GOWTH_MEM_HOME snake_case
KEBAB_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")                  # topic-routing data-quality
CAMEL_RE = re.compile(r"[A-Za-z]*[a-z][A-Z][A-Za-z0-9]*")          # CamelCase fooBar PostgreSQL
FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")                         # --apply --ws
ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")                  # DXY FTS5 BM25 OOM
_PRIORITY_REGEXES = (DOTTED_RE, SNAKE_RE, KEBAB_RE, CAMEL_RE, FLAG_RE, ACRONYM_RE)

# ── drop filters (applied after normalization) ───────────────────────────
_VERSION_RE = re.compile(r"^v?\d+(?:\.\d+)+[a-z0-9]*$")   # 3.9 v3.4 1.95.0
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEX_RE = re.compile(r"^[0-9a-f]{7,}$")                   # commit hashes
_PURE_DIGIT_RE = re.compile(r"^\d+$")
_PROSE_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)       # unicode word run (no underscore)

DEFAULT_MAX_TAGS = 7            # hard cap (settings tags.max_per_entry)
SOFT_TOTAL = 5                  # typical output 3-5; priority tokens may push to the hard cap
DEFAULT_MAX_FRONTMATTER = 15
_CASE_BOOST = 1.15             # Capitalized prose token = proper-noun signal
_BIGRAM_FACTOR = 0.7          # adjacent content pair → noun-phrase-ish bigram candidate

# ── stopwords: ~200 EN + ~100 VI ─────────────────────────────────────────
_EN_STOP = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "aren", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "can", "cannot", "could",
    "couldn", "did", "didn", "do", "does", "doesn", "doing", "don", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn", "has",
    "hasn", "have", "haven", "having", "he", "her", "here", "hers", "herself",
    "him", "himself", "his", "how", "i", "if", "in", "into", "is", "isn", "it",
    "its", "itself", "just", "let", "me", "more", "most", "mustn", "my",
    "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only",
    "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own",
    "same", "shan", "she", "should", "shouldn", "so", "some", "such", "than",
    "that", "the", "their", "theirs", "them", "themselves", "then", "there",
    "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "wasn", "we", "were", "weren", "what", "when", "where",
    "which", "while", "who", "whom", "why", "will", "with", "won", "would",
    "wouldn", "you", "your", "yours", "yourself", "yourselves",
    # common technical / prose filler that adds no tag value
    "also", "already", "always", "another", "anyone", "anything", "around",
    "back", "become", "been", "being", "best", "better", "come", "current",
    "currently", "done", "even", "every", "example", "first", "get", "give",
    "goes", "going", "good", "got", "great", "instead", "keep", "kept", "know",
    "known", "last", "later", "least", "less", "like", "likely", "made", "make",
    "makes", "making", "many", "may", "maybe", "mean", "means", "much", "must",
    "need", "needs", "never", "new", "next", "note", "old", "one", "onto",
    "part", "per", "place", "put", "really", "right", "run", "runs", "said",
    "say", "see", "seen", "set", "several", "since", "still", "sure", "take",
    "taken", "takes", "tell", "thing", "things", "think", "thus", "time",
    "times", "today", "together", "took", "toward", "try", "trying", "two",
    "upon", "use", "used", "uses", "using", "via", "want", "way", "ways",
    "well", "went", "whether", "within", "without", "work", "works", "yet",
}
_VI_STOP = {
    "là", "và", "của", "được", "không", "cho", "với", "này", "đó", "các",
    "những", "để", "khi", "đã", "sẽ", "có", "trong", "ra", "lại", "thì",
    "mà", "nên", "vì", "bị", "về", "một", "cần", "phải", "như", "sau",
    "trước", "cũng", "rồi", "bằng", "hoặc", "nếu", "thế", "nào", "gì",
    "ở", "từ", "đến", "theo", "rằng", "chỉ", "còn", "nhưng", "hay", "vẫn",
    "đang", "chưa", "vào", "ai", "bởi", "bởi vì", "do", "nhé", "à", "ừ",
    "thôi", "làm", "này", "kia", "ấy", "họ", "tôi", "bạn", "mình", "chúng",
    "ta", "nó", "anh", "chị", "em", "ông", "bà", "người", "cái", "con",
    "chiếc", "việc", "điều", "khác", "nhiều", "ít", "rất", "quá", "lắm",
    "hơn", "nhất", "cùng", "giữa", "trên", "dưới", "ngoài", "đây", "đấy",
    "mỗi", "mọi", "tất", "cả", "đều", "chính", "tức", "tại", "sao", "vậy",
    "được", "nữa", "thêm", "bớt", "chứ", "à", "ơi", "ừm", "hả", "nha",
    "vâng", "dạ", "được", "xong", "hết",
}
# ASCII (diacritic-less) Vietnamese — how VI is often typed in terminals. These
# pass the ascii content-word check, so they need their own stopword entries.
_VI_ASCII_STOP = {
    "thay", "khong", "duoc", "cua", "nhu", "den", "cung", "chua", "moi",
    "mot", "nao", "nay", "kia", "vay", "roi", "het", "dung", "lam", "viec",
    "dieu", "nguoi", "minh", "chung", "nhung", "truoc", "sau", "trong",
    "ngoai", "tren", "duoi", "giua", "phai", "trai", "toi", "ban", "anh",
    "chi", "em", "ong", "ba",
}
STOPWORDS = _EN_STOP | _VI_STOP | _VI_ASCII_STOP

# Generic filesystem path components — harvested from paths like `/opt/x` but
# carrying zero retrieval signal on their own (the compound tag keeps the info).
_GENERIC_TAGS = {
    "opt", "usr", "bin", "var", "etc", "tmp", "lib", "mnt", "dev", "srv",
    "proc", "sys", "home", "root",
}


# ── strip helpers (dedup-stability + round-trip) ─────────────────────────

def format_suffix(tags: list[str]) -> str:
    """Render the inline tag suffix: `"  #a #b #c"` (empty when no tags)."""
    if not tags:
        return ""
    return "  " + " ".join(f"#{t}" for t in tags)


def strip_tags(line: str) -> str:
    """Remove a trailing run of `#tag` tokens from a single line.

    Inverse of `format_suffix` for round-trips: `strip_tags(x + format_suffix(t)) == x`
    (when x doesn't itself end in a hashtag-shaped token). Non-trailing hashtags
    are preserved.
    """
    return re.sub(r"\s*(?:#[A-Za-z0-9_][A-Za-z0-9._-]*)(?:\s+#[A-Za-z0-9_][A-Za-z0-9._-]*)*\s*$",
                  "", line)


def strip_tags_text(text: str) -> str:
    """Apply `strip_tags` to every line of *text* (used for tag-stable hashing)."""
    if not text:
        return text
    return "\n".join(strip_tags(ln) for ln in text.split("\n"))


# ── normalization + filtering ────────────────────────────────────────────

def _normalize_tag(tok: str) -> str:
    t = tok.strip().lower()
    t = re.sub(r"[^a-z0-9._-]+", "-", t)
    t = re.sub(r"-{2,}", "-", t)
    return t.strip("-.")


def _dropworthy(t: str, min_len: int) -> bool:
    if not t or len(t) < min_len:
        return True
    if _PURE_DIGIT_RE.match(t) or _VERSION_RE.match(t) or _DATE_RE.match(t) or _HEX_RE.match(t):
        return True
    alnum = re.sub(r"[._-]", "", t)
    # A token that collapsed to mostly separators, or is all digits with
    # separators (e.g. a numeric range "15-20"), carries no signal.
    if len(alnum) < 2 or alnum.isdigit():
        return True
    return False


def _clean_for_prose(text: str) -> str:
    """Strip prefix/labels/urls/emphasis. Code fences + inline code are LEFT in
    place so `_harvest_priority` can pull their identifier sub-tokens first (it
    blanks the spans afterwards)."""
    t = TYPE_PREFIX_RE.sub("", text or "")
    t = URL_RE.sub(" ", t)
    t = BOLD_LABEL_RE.sub(" ", t)
    t = EMPHASIS_RE.sub(" ", t)
    return t


def _harvest_priority(text: str) -> tuple[list[str], str]:
    """Return (ordered priority tags, text with priority spans blanked for prose).

    High-value identifiers are harvested in order of first appearance. Inline-code
    contents are treated as priority (their identifier sub-tokens are harvested).
    """
    ordered: list[tuple[int, str]] = []
    spans: list[tuple[int, int]] = []

    # Inline code first — capture identifier-ish sub-tokens inside backticks.
    for m in INLINE_CODE_RE.finditer(text):
        inner = m.group(1)
        base = m.start(1)
        for rx in _PRIORITY_REGEXES:
            for mm in rx.finditer(inner):
                ordered.append((base + mm.start(), mm.group(0)))
        # Also take plain identifier words inside code (e.g. `atomic`).
        for mm in re.finditer(r"[A-Za-z][A-Za-z0-9_]{2,}", inner):
            ordered.append((base + mm.start(), mm.group(0)))
        spans.append((m.start(), m.end()))

    # Blank code fences + inline code so the priority regexes below skip them.
    masked = list(text)
    for a, b in [(mm.start(), mm.end()) for mm in CODE_FENCE_RE.finditer(text)] + spans:
        for i in range(a, min(b, len(masked))):
            masked[i] = " "

    # Run each regex in priority order, blanking its matches BEFORE the next
    # regex so a lower-priority pattern can't re-harvest a sub-span of an
    # already-claimed token (e.g. CamelCase parts inside a snake_case name).
    for rx in _PRIORITY_REGEXES:
        for m in rx.finditer("".join(masked)):
            tok = m.group(0)
            # Pure-alpha ALL-CAPS of len>=5 is almost always prose EMPHASIS
            # ("CONTENT", "NEVER", "SUPERSESSION"), not an acronym (JSM, YACE,
            # DRY, FTS5 stay). Demote: don't harvest, don't blank — the prose
            # scorer sees it lowercased and it must earn its place by frequency.
            if rx is ACRONYM_RE and tok.isalpha() and len(tok) >= 5:
                continue
            ordered.append((m.start(), tok))
            for i in range(m.start(), min(m.end(), len(masked))):
                masked[i] = " "

    prose_text = "".join(masked)
    ordered.sort(key=lambda t: t[0])
    return [tok for _, tok in ordered], prose_text


def _score_prose(prose_text: str) -> list[str]:
    """Return prose keyword tokens ordered by descending score (deterministic).

    YAKE-lite: score = frequency × early-position boost × len(>4) boost × casing
    boost (Capitalized proper-noun signal). Adjacent content-word pairs also yield
    a noun-phrase-ish bigram candidate scored from its members.
    """
    raw = _PROSE_WORD_RE.findall(prose_text)   # original case preserved for casing
    total = len(raw)
    if not total:
        return []

    def _is_content(w: str) -> bool:
        # ASCII-only: diacritic/CJK words can't form clean tags in this scheme,
        # so they're dropped here (VI stopwords are handled separately for prose
        # that IS ascii-transliterated). Keeps garbage like "chi-n-l-c" out.
        wl = w.lower()
        return w.isascii() and wl not in STOPWORDS and len(wl) >= 3 and not wl.isdigit()

    freq: dict[str, int] = {}
    first: dict[str, int] = {}
    capital: dict[str, bool] = {}
    for i, w in enumerate(raw):
        if not _is_content(w):
            continue
        wl = w.lower()
        freq[wl] = freq.get(wl, 0) + 1
        if wl not in first:
            first[wl] = i
        if w[:1].isupper():
            capital[wl] = True

    def _pos_boost(wl: str) -> float:
        return 1.0 + (1.0 - first[wl] / total)          # earlier → higher

    def _uni_score(wl: str) -> float:
        len_boost = 1.3 if len(wl) > 4 else 1.0
        case_boost = _CASE_BOOST if capital.get(wl) else 1.0
        return freq[wl] * _pos_boost(wl) * len_boost * case_boost

    def _phrase_score(wl: str) -> float:
        # Bigram eligibility ignores the length boost so short early NOUNS
        # ("gold", "stop") aren't beaten by long VERBS ("dominate", "reduces").
        case_boost = _CASE_BOOST if capital.get(wl) else 1.0
        return freq[wl] * _pos_boost(wl) * case_boost

    scored: list[tuple[float, int, str]] = [
        (_uni_score(wl), first[wl], wl) for wl in freq
    ]

    # Noun-phrase-ish bigrams: ONLY when the two adjacent tokens are both among
    # the top-2 by phrase score (a genuine salient phrase like "gold futures" or
    # "stop loss"). Conservative on purpose — avoids junk pairs.
    top = set(sorted(freq, key=lambda wl: (-_phrase_score(wl), first[wl], wl))[:2])
    seen_bg: set[str] = set()
    for i in range(total - 1):
        a, b = raw[i].lower(), raw[i + 1].lower()
        if a == b:
            continue  # degenerate self-bigram ("pullback (pullback" → pullback-pullback)
        if not (_is_content(raw[i]) and _is_content(raw[i + 1])):
            continue
        if a not in top or b not in top:
            continue
        bg = f"{a}-{b}"
        if bg in seen_bg:
            continue
        seen_bg.add(bg)
        sc = (_uni_score(a) + _uni_score(b)) * _BIGRAM_FACTOR
        scored.append((sc, i, bg))

    # Deterministic: score desc, then first-position asc, then alphabetical.
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [w for _, _, w in scored]


def _collapse_prefixes(tags: list[str]) -> list[str]:
    """Drop any tag contained inside another surviving tag (keep the compound).

    Substring collapse is retrieval-safe: `--keyword chop` LIKE-matches the
    surviving `chop-mask`, so dropping the redundant `chop`/`build`/`tokenai`
    fragments loses nothing while keeping the tag line clean.
    """
    out: list[str] = []
    for t in tags:
        if any(o != t and len(o) > len(t) and t in o for o in tags):
            continue
        out.append(t)
    return out


def extract_tags(text: str, max_tags: int = DEFAULT_MAX_TAGS) -> list[str]:
    """Deterministically extract up to *max_tags* content tags from *text*.

    Priority identifier tokens (code, paths, CamelCase, flags, acronyms) rank
    above scored prose keywords. Returns fewer than max_tags for short/sparse
    entries — never pads.
    """
    if not text or not text.strip():
        return []
    max_tags = max(0, int(max_tags))
    if max_tags == 0:
        return []

    cleaned = _clean_for_prose(text)
    priority, prose_text = _harvest_priority(cleaned)
    prose = _score_prose(prose_text)

    ordered_candidates = priority + prose  # priority first, prose by score
    seen: set[str] = set()
    normalized: list[str] = []
    prio_norm: set[str] = set()
    for i, raw in enumerate(ordered_candidates):
        t = _normalize_tag(raw)
        is_prio = i < len(priority)
        min_len = 2 if is_prio else 3
        if _dropworthy(t, min_len):
            continue
        # Post-normalize junk guard: an UPPER-harvested stopword ("ONLY") or a
        # bare path component ("opt") is noise regardless of origin.
        if t in STOPWORDS or t in _GENERIC_TAGS:
            continue
        if t in seen:
            continue
        seen.add(t)
        normalized.append(t)
        if is_prio:
            prio_norm.add(t)

    collapsed = _collapse_prefixes(normalized)
    # Soft target 3-5 with a prose reservation: identifier-heavy entries must
    # not crowd out repeated topic words ("chop" x3 beats a fifth identifier).
    # When scored prose survives, up to 2 slots are reserved for it; priority
    # tags may otherwise run to the hard cap. Sparse entries are never padded.
    prio_tags = [t for t in collapsed if t in prio_norm]
    prose_tags = [t for t in collapsed if t not in prio_norm]
    reserve = 2 if prose_tags else 0
    prio_keep = prio_tags[:max(1, max_tags - reserve)] if prio_tags else []
    target = min(max_tags, max(SOFT_TOTAL, len(prio_keep) + reserve))
    out = list(prio_keep)
    for t in prose_tags:
        if len(out) >= target:
            break
        out.append(t)
    return out[:max_tags]


# ── frontmatter tag union (pure text transform) ──────────────────────────

def _parse_frontmatter_tags_block(fm_lines: list[str]) -> tuple[list[str], int, int]:
    """Return (existing_tags, tags_line_idx, consume_end) from frontmatter lines.

    Handles inline `tags: [a, b]` and block `tags:` + `- a` forms. Indices are
    -1 when no tags key is present. `consume_end` is exclusive.
    """
    for i, line in enumerate(fm_lines):
        m = re.match(r"^tags:\s*(.*)$", line)
        if not m:
            continue
        val = m.group(1).strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            tags = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
            return tags, i, i + 1
        if val:
            return [val.strip("'\"")], i, i + 1
        # Block form: gather following `- item` lines.
        tags = []
        j = i + 1
        while j < len(fm_lines):
            mm = re.match(r"^\s*-\s+(.+?)\s*$", fm_lines[j])
            if mm:
                tags.append(mm.group(1).strip().strip("'\""))
                j += 1
            else:
                break
        return tags, i, j
    return [], -1, -1


def _merge_tag_lists(existing: list[str], new: list[str], cap: int) -> list[str]:
    """First-seen order: existing preserved, then new not-yet-present, capped."""
    out: list[str] = []
    seen: set[str] = set()
    for t in list(existing) + list(new):
        t = (t or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= cap:
            break
    return out


def merge_frontmatter_tags(text: str, new_tags: list[str],
                           cap: int = DEFAULT_MAX_FRONTMATTER) -> str:
    """Union *new_tags* into the frontmatter `tags:` of *text*. Returns new text.

    Pure function (no IO). Preserves existing frontmatter field order and body.
    When the file has no frontmatter, prepends a minimal `tags:`-only block
    (`_validate.py --fix` later completes the required fields). Idempotent.
    """
    if not new_tags and not text.startswith("---"):
        return text

    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_inner = text[3:end].lstrip("\n")
            body = text[end + 4:]
            fm_lines = fm_inner.split("\n")
            existing, idx, consume_end = _parse_frontmatter_tags_block(fm_lines)
            merged = _merge_tag_lists(existing, new_tags, cap)
            tag_line = f"tags: [{', '.join(merged)}]"
            if idx == -1:
                new_fm = fm_lines + [tag_line]
            else:
                new_fm = fm_lines[:idx] + [tag_line] + fm_lines[consume_end:]
            return "---\n" + "\n".join(new_fm) + "\n---" + body

    # No frontmatter — prepend a minimal tags block.
    merged = _merge_tag_lists([], new_tags, cap)
    if not merged:
        return text
    return f"---\ntags: [{', '.join(merged)}]\n---\n\n" + text.lstrip("\n")


# ── inline-suffix application (write path) ───────────────────────────────

def apply_inline_tags(content: str, tags: list[str]) -> str:
    """Append the inline `#tag` suffix to the FIRST line of *content*. Idempotent."""
    if not tags:
        return content
    lines = content.split("\n")
    if not lines:
        return content
    lines[0] = strip_tags(lines[0]).rstrip() + format_suffix(tags)
    return "\n".join(lines)


# ── settings helpers ─────────────────────────────────────────────────────

def _tags_settings(settings: dict | None = None) -> dict:
    s = settings if isinstance(settings, dict) else read_settings()
    t = s.get("tags", {}) if isinstance(s, dict) else {}
    return t if isinstance(t, dict) else {}


def tags_enabled(settings: dict | None = None) -> bool:
    return bool(_tags_settings(settings).get("enabled", True))


def max_per_entry(settings: dict | None = None) -> int:
    try:
        return int(_tags_settings(settings).get("max_per_entry", DEFAULT_MAX_TAGS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TAGS


def max_frontmatter(settings: dict | None = None) -> int:
    try:
        return int(_tags_settings(settings).get("max_frontmatter", DEFAULT_MAX_FRONTMATTER))
    except (TypeError, ValueError):
        return DEFAULT_MAX_FRONTMATTER


# ── backfill (frontmatter union over existing aspect files) ──────────────

_ENTRY_FIRST_LINE_RE = re.compile(r"^\s*(?:[-*]\s*\[[a-z-]+\]|#{2,6}\s*\[[a-z-]+\])",
                                  re.IGNORECASE)
_ASPECT_GLOB = "20*-*.md"


def _iter_aspect_files(ws: str) -> list[Path]:
    root = workspace_dir(ws)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob(_ASPECT_GLOB):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in RESERVED_SUBDIRS:
            continue
        out.append(p)
    return sorted(out)


def _tags_from_entry_lines(text: str, cap: int) -> list[str]:
    """Union of tags extracted from the first line of every `[type]` entry."""
    collected: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        if not _ENTRY_FIRST_LINE_RE.match(line):
            continue
        # Preserve any pre-existing inline hashtags, then add fresh extraction.
        inline = [m.group(0)[1:].lower() for m in TAG_TOKEN_RE.finditer(line)]
        fresh = extract_tags(strip_tags(line), cap)
        for t in inline + fresh:
            t = _normalize_tag(t)
            if t and not _dropworthy(t, 2) and t not in seen:
                seen.add(t)
                collected.append(t)
    return collected


def backfill(ws: str | None = None, apply: bool = False,
             settings: dict | None = None) -> dict:
    """Frontmatter-union backfill over aspect files. Dry-run unless *apply*.

    Never rewrites entry lines (historical SHA-1 dedup + git noise). Returns a
    summary dict: {"files": N, "changed": M, "applied": bool, "plan": [...]}.
    """
    s = settings if isinstance(settings, dict) else read_settings()
    cap = max_frontmatter(s)
    wss = [ws] if ws else list_workspaces()
    plan: list[dict] = []
    changed = 0
    total = 0
    for w in wss:
        for p in _iter_aspect_files(w):
            total += 1
            try:
                text = p.read_text(errors="ignore")
            except Exception:
                continue
            new_tags = _tags_from_entry_lines(text, cap)
            if not new_tags:
                continue
            merged_text = merge_frontmatter_tags(text, new_tags, cap)
            if merged_text == text:
                continue
            final_tags = _tags_from_frontmatter(merged_text)
            plan.append({"ws": w, "path": str(p), "tags": final_tags})
            changed += 1
            if apply:
                try:
                    with file_lock(f"tags-backfill-{w}", timeout=10.0):
                        atomic_write(p, merged_text)
                except Exception:
                    atomic_write(p, merged_text)
    if apply and changed:
        _reindex_best_effort()
    return {"files": total, "changed": changed, "applied": bool(apply), "plan": plan}


def _tags_from_frontmatter(text: str) -> list[str]:
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    tags, _, _ = _parse_frontmatter_tags_block(text[3:end].split("\n"))
    return tags


def _reindex_best_effort() -> None:
    import subprocess
    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "_index.py")],
            check=False, timeout=120, capture_output=True,
        )
    except Exception:
        pass


# ── CLI ──────────────────────────────────────────────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(description="Deterministic auto-tagging (v4.0).")
    ap.add_argument("--extract", help="Extract + print tags for a single entry")
    ap.add_argument("--max", type=int, default=None, help="Override max tags for --extract")
    ap.add_argument("--backfill", action="store_true",
                    help="Union entry-line tags into aspect frontmatter")
    ap.add_argument("--ws", help="Workspace (default: all for backfill, active otherwise)")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = ap.parse_args()

    if args.extract is not None:
        n = args.max if args.max is not None else max_per_entry()
        print(" ".join(f"#{t}" for t in extract_tags(args.extract, n)))
        return 0

    if args.backfill:
        if not gowth_home().is_dir():
            print("no ~/.gowth-mem directory")
            return 0
        result = backfill(ws=args.ws, apply=args.apply)
        mode = "APPLIED" if result["applied"] else "DRY-RUN"
        for item in result["plan"]:
            rel = item["path"]
            try:
                rel = str(Path(item["path"]).relative_to(gowth_home()))
            except Exception:
                pass
            print(f"  {rel}\n    tags: [{', '.join(item['tags'])}]")
        verb = "gained" if result["applied"] else "would gain"
        print(f"retag [{mode}]: {result['changed']} of {result['files']} aspect file(s) "
              f"{verb} frontmatter tags"
              + ("" if result["applied"] else "  (re-run with --apply to write)"))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
