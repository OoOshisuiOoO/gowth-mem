# Hướng dẫn sử dụng gowth-mem (v4.9)

English: [USAGE.md](USAGE.md). Ghi chú phát hành: [SHIPPED-FEATURES.md](SHIPPED-FEATURES.md).

gowth-mem là một vault bộ nhớ duy nhất (`~/.gowth-mem`, một repo git của bạn) mà Claude Code đọc và ghi qua hook.
Phần lớn tự chạy; trang này nói cái gì tự chạy, bạn cần gõ gì, và làm gì khi một lệnh từ chối.

---

## 1. Những gì tự chạy, không cần gõ lệnh

| Khi nào | gowth-mem làm gì |
|---|---|
| Mở phiên mới | Pull vault (`auto-sync --pull-only`), giao và commit bộ quy tắc đi kèm plugin, làm mới khối quản lý trong `MEMORY.md` (quy tắc, handoff, **câu hỏi mở**, topic, con trỏ secret) |
| Bạn gửi prompt | Nếu prompt **nhắc tên một topic có thẻ** thì chèn thẻ đó (một lần mỗi phiên); nếu có entry đã tuyển chọn thật sự khớp thì chèn tối đa 3 entry (≤ 2.000 ký tự). Entry đã bị đính chính luôn đi kèm bản đính chính. Đa số prompt không được chèn gì — đó là cố ý |
| Hết một lượt | Ghi lượt vào `journal/sessions/<ngày>-<sid8>.md`; cứ 10 lượt thật thì nhờ Claude chạy một **memory teammate** nền để lưu các sự thật bền vững; cứ 15 lượt thì một **self-review** với ngữ cảnh mới; **mỗi ngày một lần cho mỗi workspace: curator** (mục 3) |
| `/compact` | Dump các lượt của người dùng vào journal hôm nay, commit, rồi pull/rebase/push |

Mọi thứ hook chèn vào đều được ghi rõ là dữ liệu từ vault, không phải mệnh lệnh. Prompt của bot / pipeline bị bỏ qua
(mục 8).

---

## 2. Cài đặt hoặc nâng cấp

**Máy mới**

```text
/plugin marketplace add OoOshisuiOoO/gowth-mem
/plugin install gowth-mem@gowth-mem
```

Khởi động lại Claude Code, rồi chạy `/mem-install` (tạo `~/.gowth-mem`, hỏi git remote, branch và cách giữ token,
push commit đầu). Máy thứ hai: `git clone <remote> ~/.gowth-mem`, rồi `/mem-ops config`.

**Nâng cấp máy đang dùng lên v4.9** — chạy đủ ba lệnh trong Claude Code:

```text
/plugin marketplace update gowth-mem
/plugin update gowth-mem@gowth-mem
/reload-plugins
```

Chỉ `marketplace update` thì mới làm mới danh mục; phải `/reload-plugins` (hoặc khởi động lại) thì hook mới được nạp.
Kiểm tra bằng `/mem-doctor` — nó cũng tự sửa registry còn trỏ vào bản cũ.

Phiên đầu tiên sau khi nâng cấp sẽ:

- ghi `shared/memory-rules.md` (+ bộ canon nghiên cứu) và một dòng `.gitattributes`, rồi **commit** chúng;
- render lại `MEMORY.md` với bản tóm tắt 12 dòng quy tắc;
- đánh dấu curator đến hạn: lần chạy đầu đọc các lượt trong log phiên của 7 ngày gần nhất
  (`curator.first_window_days`) mà chưa worklog nào xử lý — log phiên có sẵn cũng được tính, nên nó có thể chạy ngay
  ở lượt đầu tiên trong ngày.

**Nên làm một lần cho mỗi project:** `/mem-setup native` — nạp bộ nhớ qua `MEMORY.md` của chính Claude Code (được
đính kèm lại sau mỗi lần compact) thay vì bootstrap qua hook. Sau đó `/mem-cost` sẽ báo `mode=native`.

**Hai máy trùng hostname?** Đặt `"host_id"` khác nhau trong `~/.gowth-mem/config.json` của từng máy (curator chọn
một máy cho mỗi workspace theo tên này).

---

## 3. Curator hằng ngày — giữ kiến thức không bị cũ

Mỗi ngày một lần cho mỗi workspace, sau một lượt bình thường, Claude được nhắc chạy MỘT subagent nền
`gowth-mem:curator`. Bạn cứ làm việc tiếp; khi xong nó báo một dòng:

```text
curator ws=devops: 3 facts, 1 corrections, 2 cards, 2 questions (+2/-1), 1 researched — workspaces/devops/docs/worklog/2026-11-08-mac-3f2a9c.md
ask Q-1a2b3c4d: Handoff item '[next] migrate the ingress to v2' looks done — tick it?
```

Với các lượt chưa lần chạy nào xử lý, nó:

1. rà các entry được ghi trong ngày; entry sai thì thêm một **bản đính chính** (không bao giờ sửa hay xoá);
2. thêm các sự thật bền vững mà teammate bỏ sót (chưa kiểm chứng thì ghi `[hypothesis] … Verify: …`);
3. làm mới tối đa 5 **thẻ topic**;
4. mở câu hỏi cho chỗ mâu thuẫn hoặc còn thiếu, research tối đa 2 câu `research`, và biến các việc handoff đã cũ
   thành câu hỏi `ask` dạng **"có vẻ xong rồi — tick nhé?"** (không bao giờ tự sửa handoff);
5. ghi **worklog** có bằng chứng: `docs/worklog/<ngày>-<máy>-<run>.md`.

Nó chỉ ghi qua các lệnh có cổng kiểm tra của plugin. Không chạy git, không sửa file bằng tay, không gọi agent khác.

**Khi nào chạy:** lượt thật đầu tiên trong ngày sau `curator.hour` (mặc định 0), khi workspace có ít nhất
`curator.min_new_turns` (5) lượt chưa xử lý, và chỉ khi lượt đó không có việc nào khác được giao cho Claude. Không bao
giờ chạy trong phiên `claude -p` / SDK. Mỗi workspace chỉ một máy curate (máy khác thay sau 2 ngày im lặng).

**Lệnh**

```text
/mem-ops curate              # trạng thái từng workspace: lần chạy cuối, số lần lỡ, câu hỏi mở, thẻ còn TODO, lượt chưa xử lý
/mem-ops curate prep         # xem trước bản tóm tắt nó sẽ đọc (không ghi gì)
/mem-ops curate now          # chạy ngay từ phiên này (một subagent nền)
```

**Trả lời các dòng `ask` của nó:** trả lời trong chat, hoặc

```text
/mem-ops gap answer Q-1a2b3c4d "yes, done on 2026-11-07" --evidence user
```

Với câu "tick nhé?", khi bạn đồng ý Claude cũng đánh dấu việc handoff đó là `[done]`.

**Quyền.** Ở chế độ bypass thì không cần gì. Nếu không, agent nền không hỏi được bạn, nên lệnh đầu tiên bị từ chối sẽ
dừng lần chạy (`curator ws=…: blocked — …`), và hai ngày như vậy thì nó tạm dừng. Cho phép đúng các lệnh của nó trong
`~/.claude/settings.json` → `permissions.allow`. `/mem-ops curate now` in ra đường dẫn thật; dạng như sau:

```json
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_curate.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_query.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_topic.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_gaps.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_lesson.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_card.py:*)",
"Read(~/.gowth-mem/**)",
"Read(//<plugin>/**)"
```

`<vault>` là đường dẫn tuyệt đối của vault, `<plugin>` là thư mục cài plugin — trong đó có số phiên bản, nên sau mỗi
lần nâng cấp phải sửa lại các quy tắc này. Thêm `"WebSearch"` / `"WebFetch(domain:<trang docs>)"` nếu muốn nó research
trên web. (Đã kiểm chứng trên Claude Code 2.1.301 ở chế độ quyền mặc định.)

**Chỉnh / tắt** — `~/.gowth-mem/settings.json`:

```jsonc
"curator": {
  "enabled": true,          // false = không bao giờ chạy (mọi thứ khác vẫn hoạt động)
  "hour": 0,                // giờ sớm nhất trong ngày
  "min_new_turns": 5,
  "hosts": [],              // ví dụ ["mac-studio"]: chỉ các máy này curate
  "model": "sonnet",
  "max_cards": 5, "max_research": 2, "max_research_tries": 3,
  "max_prep_chars": 80000   // kích thước bản tóm tắt; ~100 lượt mỗi lần chạy
}
```

Nếu thấy `[gowth-mem:curator-paused ws=…]`: hai ngày đã nhận việc mà không có worklog (không có công cụ subagent,
bị từ chối quyền, phiên kết thúc sớm). Chạy `/mem-ops curate now` trong một phiên dài hơn.

---

## 4. "Phải research" — sổ câu hỏi

Khi Claude không biết và chưa tìm ra ngay, điều đó trở thành một câu hỏi được theo dõi trong
`<ws>/docs/questions.md` thay vì một phỏng đoán:

- `research` — agent tự tìm được câu trả lời (vault, code, tài liệu chính thức, web);
- `ask` — chỉ bạn biết (ý định, ai sở hữu, chính sách, "đã làm X chưa?").

Câu hỏi mở hiện trong `MEMORY.md` ở mục `## Open questions` (`ask` trước), nên phiên nào cũng thấy.

```text
/mem-ops gap add research "Does pelican-router v2 support gRPC health checks?" --why "the v2 migration depends on it"
/mem-ops gap add ask "Which AWS account should the staging bucket live in?"
/mem-ops gap list                   # câu hỏi mở, cũ nhất trước
/mem-ops gap list --closed          # lịch sử kèm câu trả lời
/mem-ops gap answer Q-xxxxxxxx "<câu trả lời>" --evidence "<đường dẫn vault của entry [ref] | URL | commit | user>"
/mem-ops gap tried Q-xxxxxxxx --note "no primary source yet"
/mem-ops gap close Q-xxxxxxxx --reason obsolete --evidence "the feature was dropped"
```

Trả lời câu `research`: trước tiên lưu sự thật thành `[ref]` có `Source:` (mục 6), rồi trả lời bằng đường dẫn mà lệnh
lưu đã in ra (URL hoặc commit cũng được). Chỉ tên file hoặc `file:dòng` sẽ bị từ chối là `weak_evidence`; câu `ask`
trả lời bằng `--evidence user`. Curator research tối đa 2 câu mỗi lần và chuyển câu đã thử thất bại 3 lần thành `ask`.

Lưu ý: nếu vault bật `gate.english_only` thì câu hỏi và entry phải viết bằng tiếng Anh.

---

## 5. Thẻ topic — sự thật hiện tại của một topic

Thẻ nằm trong `00-README.md` của topic (trong phần TL;DR): năm mục ngắn, tối đa 2.000 ký tự.

```text
### Core        topic này hiện là gì (1–3 dòng)
### State       các sự thật có ngày, mới nhất trước
### Routing     câu hỏi → mở file nào trước
### Gotchas     các bài học đã trả giá, kèm ngày
### Sources     path:dòng, URL hoặc commit
```

```text
/mem-topic card pelican-router              # xem
/mem-topic card pelican-router set          # Claude viết/thay thẻ (thẻ cũ được lưu vào .history/)
/mem-topic card pelican-router summary "Ingress gateway on the edge cluster; v1 until gRPC checks land"
/mem-topic card check                       # trạng thái mọi topic: ok, none, todo-summary, stale, over-budget, invalid
```

Thẻ được chèn khi prompt **nhắc tên** topic — slug (`pelican-router`), slug viết thành chữ (`pelican router`) hoặc
alias — một lần mỗi phiên. Từ chung chung không bao giờ kích hoạt (`test`, `config`, `next step` …). Thẻ chỉ được ghi
qua các lệnh này (MOC rebuild không đụng tới); curator làm mới tối đa 5 thẻ mỗi lần. Nếu lệnh trả về
`rejected:<lý do>` thì chưa có gì được ghi — lý do cho biết cần sửa gì.

---

## 6. Lưu và đính chính bộ nhớ

Lưu một entry (cổng kiểm tra + định tuyến + chống trùng + tag + index):

```text
/mem-save [decision] "Keep pelican-router v1 until v2 has gRPC health checks, because v2 drops the legacy probe"
/mem-save [ref] "pelican-router batch upstream timeout is 30s. Source: deploy/pelican.yaml@3f2a9c81d0e4" --topic pelican-router
/mem-save            # không tham số: distill cả phiên này vào các topic
```

Mỗi loại có phần bắt buộc: `[decision]` lý do ("because …"), `[ref]` có `Source:`, `[tool]` phiên bản hoặc cú pháp
chính xác, `[hypothesis]` có `Verify:`, `[goal]` có `Status:` và `Done when:`. Kết quả là `written`, `duplicate` (đã có
— không cần làm gì) hoặc `rejected:<quy tắc>` (sửa theo quy tắc đó rồi lưu lại).

**Bộ nhớ có chỗ sai?** Đừng sửa hay xoá. Lưu một bản đính chính cùng loại, chỉ rõ entry bị sai:

```text
/mem-save [decision] "Correction: batch upstream timeout 60s → 30s (2026-11-07; Corrects: workspaces/demo/pelican-router/2026-10-20-timeouts.md; Source: deploy/pelican.yaml@3f2a9c81d0e4)"
```

Sau đó recall hiện `(corrected <ngày>: <path>)` cạnh entry cũ và mang bản đính chính theo. Bản đính chính vẫn phải có
phần bắt buộc của loại đó; một `[hypothesis]` đã có kết luận thì thành `[ref]`. Nhớ làm mới thẻ topic.

**Tìm lại:** `/mem-recall <câu hỏi>` (BM25, không dùng LLM; `--topic <slug>`, `--keyword <tag>`, `--days N`,
`--type decision|ref|…`).

Bộ quy tắc đầy đủ mà Claude làm theo — thứ tự tra cứu, nhãn bằng chứng (CONFIRMED / UNVERIFIED / FALSE POSITIVE), loại
kiến thức nào đặt ở đâu — nằm ở `~/.gowth-mem/shared/memory-rules.md`. Bạn có thể sửa: bản đã sửa sẽ không bị các bản
phát hành sau ghi đè.

---

## 7. Dùng hằng ngày

- Đầu phiên: xem mục **Open questions** trong `MEMORY.md` — trả lời các câu `ask` bạn biết
  (`/mem-ops gap answer <id> "<câu trả lời>" --evidence user`).
- Khi Claude nói không biết: nó phải tự tìm hoặc thêm câu hỏi `research` / `ask` — nếu nó đoán bừa thì nhắc nó.
- Khi bạn sửa Claude, nó ghi một bản đính chính; curator cũng bắt được những lần đính chính bị bỏ sót.
- Cuối phiên: `/mem-handoff` (hoặc chỉ cần bảo Claude cập nhật handoff).
- Thỉnh thoảng: `/mem-ops curate` (trạng thái), `/mem-topic card check`, `/mem-cost`.

---

## 8. Bot, pipeline và `claude -p`

- `GOWTH_MEM_OFF=1` trong môi trường của một tiến trình sẽ tắt mọi hook gowth-mem cho tiến trình đó (không đọc, không
  ghi gì). Dùng cho bot điều khiển Claude Code (phiên tmux, các stage `claude -p`):

  ```bash
  GOWTH_MEM_OFF=1 claude -p "…"
  ```

- Không đặt biến đó thì prompt có dạng stage pipeline vẫn bị bỏ qua (không đếm, không ghi log, không recall, không
  dump): `STAGE=…`, `### TASK … ###`, `### DAILY … ###`, prompt tóm tắt phiên của Claude Code — kể cả khi nằm trong
  khối dán `<pasted_content id=…>`. Thêm tiền tố của bot riêng bạn trong settings:

  ```jsonc
  "capture": { "pipeline_prefixes": ["[deploy-bot]", "Nightly report for"] }   // tiền tố nguyên văn, không phải regex
  ```

---

## 9. Lấy lại entry bị mất ở các bản cũ

Trước v4.9, khi lưu trữ các aspect cũ, `lessons.md` chỉ giữ lại các bullet `- [type]`; các khối `## [type]` và dòng
`[type]` trần chỉ còn trong `.archive/` trên máy và trong lịch sử git. v4.9 cứu mọi dạng trước khi lưu trữ. Để lấy lại
các entry đã bị lưu trữ (chạy trên mọi máy từng lưu trữ aspect):

```text
/mem-ops forget --resalvage-archive --dry-run     # chỉ đếm
/mem-ops forget --resalvage-archive               # thêm chúng vào lessons.md của từng topic
/mem-topic regen-index
```

Lệnh này không xoá, không di chuyển, không ghi lại gì trong `.archive/`, bỏ qua entry đã có, và chạy lần hai thì không
thêm gì.

---

## 10. Xử lý sự cố

| Bạn thấy | Làm gì |
|---|---|
| `curator ws=…: blocked — <lệnh> …` | thêm các quy tắc cho phép ở mục 3 (hoặc dùng bypass), rồi `/mem-ops curate now` |
| `[gowth-mem:curator-paused ws=…]` | `/mem-ops curate now` trong một phiên dài hơn |
| Nhiều ngày curator không chạy | `/mem-ops curate` — nó cho biết lý do (ít hơn 5 lượt mới, máy khác đang curate, `enabled: false`, danh sách `hosts` không có máy này) |
| `rejected:weak_evidence` | câu trả lời `research` cần đường dẫn vault của entry `[ref]`, một URL hoặc một commit |
| `rejected:not_english` | `gate.english_only` đang bật: viết entry bằng tiếng Anh |
| `rejected:control_token` / `rejected:private_tag` | nội dung trích một directive `[gowth-mem:` hoặc thẻ `<private>` — viết lại |
| `rejected:lock_timeout` | phiên khác đang ghi — chạy lại đúng lệnh đó |
| Thẻ không bao giờ được chèn | prompt phải nhắc tên topic (slug hoặc slug viết thành chữ); `/mem-topic card check` phải báo `ok` |
| Nhắc `SYNC-CONFLICT.md` | `/mem-sync resolve` (nó hoàn tất rebase bằng `_conflict.py --continue`) |
| Hook vẫn chạy như bản cũ | `/reload-plugins`, rồi `/mem-doctor` |
| Vault trông có gì đó sai | `/mem-ops validate`, `/mem-ops gate --scan`, `/mem-cost` |
