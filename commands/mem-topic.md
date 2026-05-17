---
description: List, inspect, or route a content snippet to a topic FOLDER (v3.0). Read-only by default; --regen-index rewrites every topic 00-README.md and the workspace MOC.
---

Manage the topic registry under the active workspace (`~/.gowth-mem/workspaces/<ws>/`).

v3.0: a TOPIC is a FOLDER. Each `<slug>/` contains:
- `00-README.md` — MOC (TL;DR + Aspects auto + Cross-links manual)
- `YYYY-MM-DD-<aspect>.md` — dated aspect files (the actual content)
- `lessons.md` — per-topic 5-field ledger

Subcommands (mutually exclusive):

- **list** (default if no args): show table `slug | title | aspects | last touched`. Run:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --list
  ```

- **inspect `<slug>`**: open `~/.gowth-mem/workspaces/<ws>/<slug>/00-README.md` and show first 80 lines, followed by the names of all sibling dated aspect files (newest-first).

- **route `<text>`**: show which topic folder slug `_topic.route()` would pick and which dated aspect file it would append to:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --route "<text>"
  ```
  Output format: `slug=<topic> aspect=<today-aspect> path=workspaces/<ws>/<slug>/YYYY-MM-DD-<aspect>.md`

- **regen-index**: regenerate `topics/_index.md` (workspace-wide topic listing) and every topic's `00-README.md` from current files:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --ws "<ws>"
  ```

- **ensure `<slug>`**: create `workspaces/<ws>/<slug>/00-README.md` if missing, with the v3 topic-readme skeleton (frontmatter + TL;DR + Aspects auto + Cross-links manual). Use when manually starting a new topic:
  ```bash
  # Generic (renders topic-readme-skeleton.md):
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --ensure "<slug>"

  # With initial metadata:
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --ensure "<slug>" \
      --title "StarRocks FE OOM" --parents starrocks \
      --summary "FE JVM heap exhausted → metadata write fail"
  ```

  `ensure` only creates the topic FOLDER + `00-README.md` MOC. It does NOT spawn a dated aspect file. The first `[exp]`/`[ref]`/`[decision]` line routed in via `mems` creates today's `YYYY-MM-DD-<aspect>.md`.

## Routing rules (v3)

- The router uses keyword overlap (≥3 common words) against existing topic `00-README.md` TL;DR + Cross-links.
- New slugs come from the top-2 distinctive keywords.
- Default fallback topic: `misc` (configurable via `settings.topic_routing.default_topic`).
- Default fallback aspect: `note` (configurable via `settings.topic_routing.default_aspect`).
- Reserved subdirs blocked as topic slugs: `docs`, `journal`, `skills`, `research`.
- Reserved aspect names blocked: `readme`, `lessons`, `00-readme`.

## Side-channels

- `[secret-ref] <env-var>` lines bypass topic routing → appended to `shared/secrets.md`.
- `[skill-ref] <slug>` lines bypass topic routing → appended to `workspaces/<ws>/skills/<slug>.md`.
- `[lesson]` entries are written via `/mem-lesson`, not `/mem-topic`. They land in `<slug>/lessons.md`.
