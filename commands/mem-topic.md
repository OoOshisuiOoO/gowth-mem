---
description: List, inspect, or route a content snippet to a topic slug. Read-only by default; --regen-index rewrites topics/_index.md.
---

Manage the topic registry under `~/.gowth-mem/topics/`.

Subcommands (mutually exclusive):

- **list** (default if no args): show table `slug | title | last touched`. Run:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --list
  ```

- **inspect `<slug>`**: open `~/.gowth-mem/topics/<slug>.md` and show first 80 lines.

- **route `<text>`**: show which topic slug `_topic.route()` would pick for that text:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --route "<text>"
  ```

- **regen-index**: regenerate `topics/_index.md` from the current files:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --regen-index
  ```

- **ensure `<slug>`**: create `topics/<slug>.md` if missing (with frontmatter). Use when manually starting a new topic:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --ensure "<slug>"
  ```

The router uses keyword overlap (≥3 common words) against existing topic files. New slugs come from the top-2 distinctive keywords. Default fallback is `misc` (configurable in `settings.json` → `topic_routing.default_topic`).
