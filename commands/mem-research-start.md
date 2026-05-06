---
description: Start a deep-research campaign on an external repo/system. Scaffolds workspaces/<ws>/research/<topic>/raw/_locate.md template — fill it with the source-code map, then add raw/<file>.md notes (line-by-line, every claim cites <repo:file:line>). When raw/ has ≥1 note, run /mem-research-distill <topic> to write distilled.md.
---

Scaffold a new research topic under the active workspace.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_research.py" --start "$1"
```

Workflow once scaffolded:

1. Edit `raw/_locate.md` — fill the source-code map: clone path, package layout, file table (lines + role per file), open questions, priority reads.
2. Per source file, create `raw/<file_basename>.md`:
   - Quote ≤20 lines per snippet
   - Every factual claim must have a `repo:file:line` ref
   - Mark unverified claims as `[INFERRED]`
3. When ≥1 raw note exists, run `/mem-research-distill <topic>` to scaffold the 1-page distillation + run the quality gate.

Quality gate enforced by `/mem-research-distill`:
- distilled.md < 800 words
- Every raw note has at least one source ref (`source_file:` frontmatter, body `<repo:file:line>`, or `Source:` line)

Storage: `~/.gowth-mem/workspaces/<active_ws>/research/<topic>/raw/`.
