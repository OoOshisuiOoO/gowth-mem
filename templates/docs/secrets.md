# docs/secrets.md

Resource registry. **POINTER ONLY** — never the actual value.

## Schema

Each entry MUST start with `[secret-ref]` prefix.

| Field | Required |
|---|---|
| Name (env var or file path) | yes |
| What it is (1 line) | yes |
| How to obtain | yes |
| Value | **NEVER** |

Optional metadata:
- `valid_until: YYYY-MM-DD` for time-limited credentials (mem-prune deletes when expired)
- `(rotated)` marker for rotated keys

## Hard rules

- KHÔNG commit value thật của API key / token / password vào git.
- File chứa secret → `.gitignore` ngay khi tạo.
- Khi rotate key → mark old entry with `(rotated)` for prune; ADD new entry.
- Khi credential expires → add `valid_until: <date>` so mem-prune cleans automatically.

## Env vars

| Tên biến | Mô tả | Lấy ở đâu |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API access | https://platform.openai.com/api-keys |
| `ANTHROPIC_API_KEY` | Claude API access | https://console.anthropic.com/settings/keys |
| `MT5_LOGIN` | MetaTrader 5 login | broker portal |

## Files (gitignored)

| Path | Nội dung |
|---|---|
| `.env` | All env vars for development |
| `~/.config/sierra/credentials` | Sierra Chart broker creds |

## Shared resources (provided by user)

- [secret-ref] (URL / file path) — (purpose) — (access method, no value)

## Examples

```markdown
- [secret-ref] OPENAI_API_KEY — needed by hooks/scripts/_embed.py — get from platform.openai.com

- [secret-ref] (rotated) old MT5_PASSWORD — superseded 2026-04-15

- [secret-ref] BROKER_API_TOKEN — temporary trial — valid_until: 2026-06-30
```

mem-prune deletes `(rotated)` and expired `valid_until:` entries automatically.
