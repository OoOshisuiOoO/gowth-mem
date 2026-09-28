"""v4.8 Task 2: `_handoff.digest` — the newest-first handoff slice for MEMORY.md.

The audit found three real handoff shapes: newest-first (personal, devops),
mixed order (trade: newest section at the top, later auto-journal sections
appended at the tail), and a 52 KB blockquoted preamble above older sections (idol-ai). The digest must
put the newest dated section first for all three, keep live bullets
([blocker]/[doing]/[next]/[thread]) ahead of [done] inside it, drop blank
lines, and cut long lines, so the block stays useful inside a 60-line budget.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _handoff  # type: ignore  # noqa: E402

NEWEST_FIRST = """# handoff — demo

## 2026-09-13 snapshot
- host:mac 2026-09-13 [done] shipped v4
- host:mac 2026-09-13 [blocker] waiting on token

## 2026-09-11 snapshot
- host:mac 2026-09-11 [done] old work

## Notes
- structural section, undated
"""

MIXED = """# handoff — trade

## 2026-09-11 status
- host:mac 2026-09-11 [done] first

## Notes
- keep me after dated sections

## 2026-09-13 auto-journal
- host:mac 2026-09-13 [next] newest appended at the tail
"""


# The live idol-ai shape (review I3): H1, then a long blockquoted preamble of
# delta updates prepended newest-first (dates up to 08-20 inside), THEN the
# dated `##` sections whose newest is older (08-18).
IDOL_SHAPE = """# handoff — idol

> ---
> **Cập nhật 2026-08-19 ~16:45 (host:NDP) — delta lượt này**
>
> **✅ closed P5+P6** — credits flow `819dda3`, 456 tests pass.
> **📌 NEXT:** cookie expires `2026-08-20T01:28:16Z`, still not refreshed.
> (1) first next item
> (2) second next item

## 2026-08-18 — restructure video-gw (host:Mac)
- host:Mac 2026-08-18 [done] tier-2 trait + routing stands

## 2026-08-15 — FE live
- host:Mac 2026-08-15 [done] pages deploy
"""

# The live devops shape (review I3): a dated section, `###` sub-headings, bullets
# whose INDENTED continuation lines carry dates inside wikilinks.
DEVOPS_SHAPE = """# handoff — devops

## host:NDP 2026-09-28 — proveny dev S3 incident fixed

### Doing
- **Fixed**: api/worker CrashLoop from keys vanishing from Infisical dev.
  Full detail + rollback: [[../proveny/2026-09-28-dev-s3-credentials]].
- **Shipped**: scraper deployed + verified on dev; prod has no
  room for the scraper's 2 CPU/3Gi ask. See [[../proveny/2026-09-28-capacity]].
- New pointer recorded: admin credential at
  `~/credentials/fg/coroot-admin.txt` (verified login 2026-09-28) — see secrets.md.

### Next
- Kiên to land durable prune-on-boot.

## host:NDP 2026-09-21 — older
- host:NDP 2026-09-21 [done] older work
"""


class HandoffDigestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_digest_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.docs = Path(self.tmp) / "workspaces" / "demo" / "docs"
        self.docs.mkdir(parents=True)

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text: str) -> None:
        (self.docs / "handoff.md").write_text(text)

    def test_missing_file_is_empty(self):
        self.assertEqual(_handoff.digest("demo"), [])

    def test_newest_section_first_and_live_bullets_first(self):
        self._write(NEWEST_FIRST)
        lines = _handoff.digest("demo")
        self.assertEqual(lines[0], "## 2026-09-13 snapshot")
        self.assertIn("[blocker]", lines[1])
        self.assertIn("[done] shipped", lines[2])
        self.assertNotIn("", lines)

    def test_mixed_order_puts_newest_first_then_older_then_undated(self):
        self._write(MIXED)
        lines = _handoff.digest("demo")
        headers = [l for l in lines if l.startswith("## ")]
        self.assertEqual(headers, ["## 2026-09-13 auto-journal", "## 2026-09-11 status", "## Notes"])

    def test_headerless_body_yields_first_nonblank_lines(self):
        # a file with no `##` at all (synthetic): body in file order, H1 dropped
        body = "# handoff — idol\n\n" + "".join(f"line {i} some state text\n\n" for i in range(300))
        self._write(body)
        lines = _handoff.digest("demo")
        self.assertEqual(len(lines), 60)
        self.assertNotIn("", lines)
        self.assertEqual(lines[0], "line 0 some state text")
        self.assertNotIn("# handoff — idol", lines)

    def test_preamble_deltas_come_first_in_file_order(self):
        """Review I3 (idol-ai): the preamble beyond the H1 holds the NEWEST
        content (08-20 inside) and must lead the digest, in file order — the
        first cut dropped it and presented the 08-18 section as current."""
        self._write(IDOL_SHAPE)
        lines = _handoff.digest("demo")
        self.assertNotIn("# handoff — idol", lines)
        self.assertTrue(lines[0].startswith("> "), lines[:3])
        i_delta = next(i for i, l in enumerate(lines) if "Cập nhật 2026-08-19" in l)
        i_next1 = next(i for i, l in enumerate(lines) if "(1) first next item" in l)
        i_next2 = next(i for i, l in enumerate(lines) if "(2) second next item" in l)
        i_sec = lines.index("## 2026-08-18 — restructure video-gw (host:Mac)")
        self.assertLess(i_delta, i_next1)
        self.assertLess(i_next1, i_next2)
        self.assertLess(i_next2, i_sec)
        self.assertLess(i_sec, lines.index("## 2026-08-15 — FE live"))

    def test_bullets_keep_their_continuation_lines_and_subheadings(self):
        """Review I3 (devops): a bullet and its indented continuation lines are
        ONE item; `###` sub-headings stay in place; dates inside continuation
        lines never hoist them above their bullet."""
        self._write(DEVOPS_SHAPE)
        lines = _handoff.digest("demo")
        self.assertEqual(lines[:9], [
            "## host:NDP 2026-09-28 — proveny dev S3 incident fixed",
            "### Doing",
            "- **Fixed**: api/worker CrashLoop from keys vanishing from Infisical dev.",
            "  Full detail + rollback: [[../proveny/2026-09-28-dev-s3-credentials]].",
            "- **Shipped**: scraper deployed + verified on dev; prod has no",
            "  room for the scraper's 2 CPU/3Gi ask. See [[../proveny/2026-09-28-capacity]].",
            "- New pointer recorded: admin credential at",
            "  `~/credentials/fg/coroot-admin.txt` (verified login 2026-09-28) — see secrets.md.",
            "### Next",
        ])
        self.assertEqual(lines[9], "- Kiên to land durable prune-on-boot.")
        self.assertEqual(lines[10], "## host:NDP 2026-09-21 — older")

    def test_long_line_is_cut_with_ellipsis(self):
        self._write("## 2026-09-13 s\n- host:mac 2026-09-13 [doing] " + "x" * 400 + "\n")
        lines = _handoff.digest("demo")
        self.assertEqual(len(lines[1]), 160)
        self.assertTrue(lines[1].endswith("…"))

    def test_flat_list_with_old_undated_lines_below(self):
        # the live personal workspace: dated `- host:` bullets, a blank line, then
        # old undated `host:` lines — the digest must not surface the old lines first
        self._write("## Entries\n- host:mac 2026-07-11 [doing] july work\n- host:mac 2026-09-28 [done] newest work\n"
                    "\nhost:mac [done] ancient undated line\nhost:mac [next] ancient next\n")
        lines = _handoff.digest("demo")
        self.assertEqual(lines[0], "## Entries")
        self.assertIn("newest work", lines[1])
        self.assertIn("july work", lines[2])
        self.assertIn("ancient undated line", lines[3])

    def test_max_lines_respected(self):
        self._write("## 2026-09-13 s\n" + "".join(f"- host:mac 2026-09-13 [done] item {i}\n" for i in range(200)))
        self.assertEqual(len(_handoff.digest("demo", max_lines=60)), 60)
        self.assertEqual(len(_handoff.digest("demo", max_lines=15)), 15)


if __name__ == "__main__":
    unittest.main()
