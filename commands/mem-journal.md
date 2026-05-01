---
description: Open today's journal entry (docs/journal/YYYY-MM-DD.md). Creates from template if missing. Use to log raw observations, questions, wins, pains.
---

Open or create today's journal entry.

Run with the Bash tool:

```bash
WS="${CLAUDE_PROJECT_DIR:-$PWD}"
TODAY=$(date +%Y-%m-%d)
JOURNAL_DIR="$WS/docs/journal"
JOURNAL="$JOURNAL_DIR/$TODAY.md"
mkdir -p "$JOURNAL_DIR"
if [ ! -f "$JOURNAL" ]; then
  cp "${CLAUDE_PLUGIN_ROOT}/templates/journal-day.md" "$JOURNAL"
  # Replace YYYY-MM-DD placeholder with today's actual date in the heading
  python3 -c "
import sys
p='$JOURNAL'
t='$TODAY'
content = open(p).read().replace('YYYY-MM-DD', t)
open(p, 'w').write(content)
"
  echo "created: docs/journal/$TODAY.md"
else
  echo "exists: docs/journal/$TODAY.md"
fi
cat "$JOURNAL"
```

After showing the journal, ask the user what to log and under which section (Logs / Questions / Wins / Pains).

For Logs entries, prefix with timestamp `HH:MM — `.

This is layer 1 (raw daily journal). At end of day or before `/compact`, run `/mem-distill` to promote signal entries up to the curated layer (`docs/exp.md` / `docs/ref.md` / `docs/tools.md`).
