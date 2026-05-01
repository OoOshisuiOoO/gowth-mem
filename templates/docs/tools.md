# docs/tools.md

Tool registry. Ghi sau mỗi lần dùng / cài tool mới.

Trước khi viết script Python/Bash → tra file này. Có tool → dùng tool. Không có → mới được tự code, và phải ghi vào đây lý do "không có tool nào cover".

## Installed

| Tool | Version | Cài bằng | Use case |
|---|---|---|---|
| (e.g. ripgrep) | 14.0 | `brew install rg` | grep nhanh, recursive default |

## Cú pháp đã work

- `rg "pattern" -t py` — chỉ search file Python
  Gotcha: rg ignore .gitignore mặc định; thêm `-uu` để search hết.

## Tools đã thử nhưng không dùng

- (tool) — (lý do bỏ: chậm / lỗi / overlap với cái đã có)
