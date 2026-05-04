---
description: Capture an experience entry (lesson / troubleshooting / postmortem) into <topic>/lessons.md
argument-hint: "[topic-slug] | <symptom> -- <tried> -- <root cause> -- <fix> [-- source]"
---

# /mem-lesson

Append a 5-field lesson entry to the active workspace's matching topic. Entries land in `workspaces/<ws>/<topic-folder>/lessons.md` (created on first use; one ledger per topic folder).

## Schema (5 fields, all canonical-source-cited)

| Field | What | Source |
|---|---|---|
| **Symptom** | observable error / behavior | AWS EKS Troubleshooting + steveyegge/beads TROUBLESHOOTING.md |
| **Tried** | what was attempted, in order | Stack Overflow rhetorical convention + GitHub bug-report template |
| **Root cause** | 1-line answer (optional 5-Whys chain) | 5 Whys (Toyoda/Ohno) + man-pages(7) ERRORS |
| **Fix** | working command/patch/config | Stripe error docs (Solutions field) + Beads Fix |
| **Source** | commit SHA / file:line / URL (optional) | Stripe doc_url + AI-trade `[ref]` Source rule |

## Modes

### A. One-liner (fast path)

```
/mem-lesson <symptom> -- <tried> -- <root cause> -- <fix>
/mem-lesson <symptom> -- <tried> -- <root cause> -- <fix> -- <source>
/mem-lesson --topic <slug> <symptom> -- <tried> -- <root cause> -- <fix>
```

`--topic <slug>` forces the destination topic. Otherwise auto-route via `_topic.route` (keyword overlap with existing topic files in active workspace).

### B. Interactive

```
/mem-lesson
```

Prompt user 5 fields sequentially. Useful when fields are multi-line or contain `--`.

## Steps

```bash
ARG="$*"
# Detect topic flag
TOPIC_FLAG=""
if [[ "$ARG" == --topic\ * ]]; then
  TOPIC_SLUG=$(echo "$ARG" | awk '{print $2}')
  ARG=$(echo "$ARG" | sed -E 's/^--topic [^ ]+ //')
  TOPIC_FLAG="--topic $TOPIC_SLUG"
fi

# One-liner detection: contains " -- "
if [[ "$ARG" == *" -- "* ]]; then
  python3 - "$ARG" $TOPIC_FLAG <<'PYEOF'
import sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
from _lesson import parse_oneliner, append_lesson

text = sys.argv[1]
topic = None
if len(sys.argv) > 3 and sys.argv[2] == "--topic":
    topic = sys.argv[3]

parsed = parse_oneliner(text)
if parsed is None:
    print("Malformed. Need 4-5 fields separated by ' -- '. Use interactive: /mem-lesson")
    sys.exit(2)

written = append_lesson(topic=topic, **parsed)
print(f"lesson saved: {written}")
PYEOF
else
  # Interactive flow — Claude prompts user 5 questions, then calls _lesson.py
  echo "Interactive mode. Asking user for 5 fields…"
fi

# Refresh MOC
WS=$(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" active)
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --ws "$WS"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py"
```

## Interactive flow (Claude executes when no one-liner)

When user types bare `/mem-lesson`, Claude asks (in order):

1. **Symptom** — what did you observe? (1 line; this becomes the H2 heading)
2. **Tried** — what did you do? (bullet list OK)
3. **Root cause** — 1 line (optional `because... because... because...` for 5 Whys)
4. **Fix** — the working command/patch/config
5. **Source** — link/commit/file:line (optional, press Enter to skip)

Then Claude shells out:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_lesson.py" \
    --symptom "$SYMPTOM" --tried "$TRIED" --root "$ROOT" --fix "$FIX" \
    ${SOURCE:+--source "$SOURCE"} ${TOPIC:+--topic "$TOPIC"}
```

## Storage layout

```
workspaces/<ws>/<topic>/lessons.md
```

Per topic folder, NOT per sub-aspect file. If user logs a lesson while routing matches `starrocks/operator.md`, the lesson lands in `starrocks/lessons.md` (folder-level ledger) — a single ledger covers all aspects of the topic. The H2 heading prefix can mention the aspect manually if needed.

For legacy flat topics (no folder), lesson lands at `workspaces/<ws>/<slug>-lessons.md`.

## Promotion lifecycle

After ≥7 days a lesson entry stable in `lessons.md` SHOULD be distilled into the workspace's `<ws>/docs/ref.md` (the durable cross-topic facts registry). Manual via `/mem-distill` or `/mem-reflect`. No auto-cron yet.

## Hard rules

- **5 fields = MIN**: Symptom + Tried + Root cause + Fix are required. Source optional.
- **No secrets** in any field — same rule as everywhere else.
- **Newest first** — append at top of `## Entries` for fast browsing.
- **Per topic folder** — don't create lessons.md per sub-aspect; one ledger per topic.
- **Atomic write** — `_lesson.py` uses `_atomic.atomic_write` to survive concurrent sessions.
- **Auto-rebuild MOC + index** post-write so `_MAP.md` lists `lessons.md` as a sibling.
