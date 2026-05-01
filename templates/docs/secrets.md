# docs/secrets.md

Resource registry. **CHỈ pointer** — không bao giờ commit value thật.

## Env vars

| Tên biến | Mô tả | Lấy ở đâu |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API | https://platform.openai.com/api-keys |
| `MT5_LOGIN` | MetaTrader 5 login | broker portal |

## Files (gitignored)

| Path | Nội dung |
|---|---|
| `.env` | Tất cả env vars development |
| `~/.config/sierra/credentials` | Sierra Chart broker creds |

## Shared resources (provided by user)

- (URL / file path) — (gì) — (truy cập thế nào)

## Rules

- KHÔNG commit value thật của API key / token vào git.
- Khi cần value → đọc từ env var qua `os.environ.get(...)` hoặc `python-dotenv`.
- File chứa secret → `.gitignore` ngay khi tạo.
