---
description: Scaffold distilled.md (TL;DR / Architecture / Key facts / Code anchors / Delta vs current / Open questions) for a research topic, then run the quality gate (<800 words, every raw note has source ref). Run after raw/ has ≥1 note.
---

Scaffold the distilled artifact for a research topic and run the quality gate.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_research.py" --distill "$1"
```

What it does:

1. If `research/<topic>/raw/` is empty → error (run `/mem-research-start <topic>` first).
2. If `distilled.md` is missing → write a template with sections:
   - TL;DR (3 lines)
   - Architecture (ASCII diagram)
   - Key facts (`[ref] <claim>. Source: <repo:file:line>`)
   - Code anchors (Symbol / File / Line / Purpose table)
   - Delta vs current (Upstream / Ours / Gap table)
   - Open questions
3. Run the quality gate and report PASS / FAIL with reasons.

Quality gate criteria:
- `distilled.md` word count < 800
- Every raw note has ≥1 source ref (`source_file:` frontmatter, body `<repo:file:line>` pattern, or `Source:` line)
- ≥1 raw note exists

After scaffold, fill the template (replacing placeholders), then re-run `/mem-research-distill <topic>` (idempotent — won't overwrite existing distilled.md) or `/mem-research-status` to verify.
