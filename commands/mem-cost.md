---
description: Estimate the token cost of the current bootstrap (AGENTS.md + 6 docs/* + journal). Helps detect bloat before /compact runs out of room.
---

Estimate the token footprint of the gowth-mem bootstrap files.

Run with the Bash tool:

```bash
WS="${CLAUDE_PROJECT_DIR:-$PWD}"
TODAY=$(date +%Y-%m-%d)
YDAY=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d "yesterday" +%Y-%m-%d)
total=0
echo "file                                | chars | ~tokens"
echo "------------------------------------|-------|--------"
for f in "$WS/AGENTS.md" "$WS/docs/handoff.md" "$WS/docs/exp.md" "$WS/docs/ref.md" "$WS/docs/tools.md" "$WS/docs/secrets.md" "$WS/docs/files.md" "$WS/docs/journal/$TODAY.md" "$WS/docs/journal/$YDAY.md"; do
  [ ! -f "$f" ] && continue
  c=$(wc -c < "$f" | tr -d ' ')
  t=$((c / 4))
  total=$((total + c))
  rel="${f#$WS/}"
  printf "%-36s | %5d | %5d\n" "$rel" "$c" "$t"
done
echo "------------------------------------|-------|--------"
total_t=$((total / 4))
printf "%-36s | %5d | %5d\n" "TOTAL" "$total" "$total_t"
echo
echo "Cap: 60,000 chars (~15,000 tokens)."
if [ "$total" -gt 60000 ]; then
  echo "WARNING: bootstrap exceeds cap. Run /mem-distill to trim journal, /mem-promote to move topics to wiki/."
elif [ "$total" -gt 40000 ]; then
  echo "Approaching cap — consider /mem-distill soon."
fi
```

Token estimate: 1 token ≈ 4 chars (rough OpenAI/Anthropic tokenizer). Real count may vary ±20%.

Use cases:
- Before `/compact` to see if you can save more first.
- After `/mem-distill` to confirm shrinkage.
- Periodically to spot which file is bloating (often `journal/today` if you haven't distilled).
