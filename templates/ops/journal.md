---
description: Open today's journal entry in the synced vault at workspaces/<ws>/journal/<date>.md. Creates it from the template if missing. Use to log raw observations, questions, wins, pains.
---

Open or create today's journal entry for the active workspace.

Run with the Bash tool:

```bash
python3 - <<'PY'
import os, sys
from datetime import date
sys.path.insert(0, os.path.join(os.environ["CLAUDE_PLUGIN_ROOT"], "hooks", "scripts"))
from _home import active_workspace, journal_dir  # noqa: E402

ws = active_workspace()
d = journal_dir(ws)
d.mkdir(parents=True, exist_ok=True)
today = date.today().isoformat()
j = d / f"{today}.md"
if not j.is_file():
    tpl = os.path.join(os.environ["CLAUDE_PLUGIN_ROOT"], "templates", "journal-day.md")
    text = open(tpl).read().replace("YYYY-MM-DD", today) if os.path.isfile(tpl) else f"# {today}\n"
    j.write_text(text)
    print(f"created: {j}")
else:
    print(f"exists: {j}")
print(f"workspace: {ws}")
print("---")
print(j.read_text())
PY
```

After showing the journal, ask the user what to log and under which section (Logs /
Questions / Wins / Pains). For Logs entries, prefix with a timestamp `HH:MM — `.

Paths resolve through `_home.py` (honouring `GOWTH_MEM_HOME` and the active workspace).
Before v4.3 this command wrote to `$PWD/docs/journal/` — inside whatever repo you
happened to be in, so entries landed outside the vault: never git-synced to your other
machines, never indexed, invisible to every hook and to `/mem-recall`.

This is the ephemeral capture layer, not permanent storage. `_forget.py` archives raw
journal past `journal.raw_ttl_days` (default 7), salvaging curated `- [type]` entries
first — so anything worth keeping should be promoted with `/mem-distill`, or written
straight to a topic via `/mem-lesson` / `/mem-goal`, before the TTL expires.
