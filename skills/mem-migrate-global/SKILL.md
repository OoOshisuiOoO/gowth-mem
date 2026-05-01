---
name: mem-migrate-global
description: Migrate v1.0 per-workspace .gowth-mem/ folders into the v2.0 global ~/.gowth-mem/. Routes lines into topics/<slug>.md by keyword overlap; preserves provenance.
---

# mem-migrate-global

One-time migration from v1.0 (per-workspace `<ws>/.gowth-mem/`) to v2.0 (global `~/.gowth-mem/` with topic-organized content).

## Step 1 — find source workspaces

Ask the user for paths, or scan `~/Git/**` two levels deep:

```python
from pathlib import Path
candidates: list[Path] = []
root = Path.home() / "Git"
if root.is_dir():
    for ws in root.iterdir():
        gm = ws / ".gowth-mem"
        if gm.is_dir() and (gm / "AGENTS.md").is_file():
            candidates.append(ws)
print("Found v1.0 workspaces:", [str(p) for p in candidates])
```

Confirm the list with the user before proceeding.

## Step 2 — migrate per workspace

For each `ws`:

```python
import sys
from pathlib import Path
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
from _topic import route, ensure_topic
from _atomic import atomic_write
from _home import docs_dir, journal_dir, skills_dir

src = ws / ".gowth-mem"
ws_name = ws.name

stats = {"topics_created": 0, "lines_migrated": 0, "skipped": 0, "files_copied": 0}

# Topic-bound content
for fname in ("docs/exp.md", "docs/ref.md", "docs/tools.md"):
    src_file = src / fname
    if not src_file.is_file():
        continue
    for line in src_file.read_text(errors="ignore").splitlines():
        s = line.strip()
        if not s.startswith("- ["):
            continue
        slug = route(line)
        topic_path = ensure_topic(slug)
        existing = topic_path.read_text() if topic_path.is_file() else ""
        if line.strip() in existing:
            stats["skipped"] += 1
            continue
        provenance = f"  (Source: {ws_name}/{fname})"
        new_line = line.rstrip() + provenance
        atomic_write(topic_path, existing + new_line + "\n")
        stats["lines_migrated"] += 1

# Cross-topic registries
handoff_src = src / "docs" / "handoff.md"
if handoff_src.is_file():
    dst = docs_dir() / "handoff.md"
    existing = dst.read_text() if dst.is_file() else "# Handoff\n\n"
    new = []
    for line in handoff_src.read_text(errors="ignore").splitlines():
        if line.strip().startswith("- "):
            new.append(line.replace("- ", f"- host:{ws_name} ", 1))
        elif line.strip().startswith("- ["):
            new.append(line.replace("- [", f"- host:{ws_name} [", 1))
        else:
            continue  # skip non-entry lines (headers etc.)
    if new:
        atomic_write(dst, existing.rstrip() + "\n" + "\n".join(new) + "\n")

secrets_src = src / "docs" / "secrets.md"
if secrets_src.is_file():
    dst = docs_dir() / "secrets.md"
    existing_text = dst.read_text() if dst.is_file() else "# Secrets (POINTERS only — never values)\n\n"
    appended = []
    for line in secrets_src.read_text(errors="ignore").splitlines():
        if line.strip().startswith("- ") and line.strip() not in existing_text:
            appended.append(line)
    if appended:
        atomic_write(dst, existing_text.rstrip() + "\n" + "\n".join(appended) + "\n")

# Journal — copy whole files, rename collisions
jsrc = src / "journal"
if jsrc.is_dir():
    jdst = journal_dir()
    jdst.mkdir(parents=True, exist_ok=True)
    for jf in jsrc.glob("*.md"):
        target = jdst / jf.name
        if target.is_file():
            target = jdst / f"{jf.stem}-from-{ws_name}.md"
        target.write_text(jf.read_text(errors="ignore"))
        stats["files_copied"] += 1

# Skills — dedup by slug
ssrc = src / "skills"
if ssrc.is_dir():
    sdst = skills_dir()
    sdst.mkdir(parents=True, exist_ok=True)
    for sf in ssrc.glob("*.md"):
        target = sdst / sf.name
        if target.is_file():
            stats["skipped"] += 1
            continue
        target.write_text(sf.read_text(errors="ignore"))
        stats["files_copied"] += 1
```

## Step 3 — regenerate index

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --regen-index
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py" --full
```

## Step 4 — report

Print summary:
```
Migrated N workspaces:
  topics created:    <stats.topics_created>
  lines migrated:    <stats.lines_migrated>
  files copied:      <stats.files_copied>
  skipped (dups):    <stats.skipped>
```

## Step 5 — DO NOT delete v1.0 sources

Tell the user the per-workspace `<ws>/.gowth-mem/` folders are intact. They should:

1. Verify the new global topics/* look right.
2. Run `/mem-sync` to push to remote.
3. Manually `rm -rf <ws>/.gowth-mem/` once satisfied.

This is intentional: migrations should be reversible until the user commits to the new layout.
