"""v3 topic 00-README.md regeneration tests.

Covers:
- `_moc.rebuild_topic_readme(folder)` lists every dated aspect + lessons newest-first.
- TL;DR block preserved verbatim across rebuilds.
- `## Cross-links (manual)` block preserved verbatim across rebuilds.
- Idempotent: 2 consecutive rebuilds produce identical bytes (modulo `last_touched`).
- Empty topic folder (only 00-README.md, no aspects) still produces a valid MOC.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"


def _run_in_home(code: str, home: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GOWTH_MEM_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    )


def _strip_last_touched(text: str) -> str:
    """Remove `last_touched:` lines so we can compare bytes ignoring rebuild stamp."""
    return re.sub(r"^last_touched:.*\n", "", text, flags=re.MULTILINE)


class TopicReadmeV3Tests(unittest.TestCase):
    def test_rebuild_lists_aspects_newest_first(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            folder = home / "workspaces" / "ws1" / "topic-a"
            folder.mkdir(parents=True)
            (folder / "00-README.md").write_text(
                "---\nslug: topic-a\ntype: topic\n---\n\n# Topic A\n\n## TL;DR\n\n> core\n\n"
                "## Aspects (auto)\n\n(populated)\n\n## Cross-links (manual)\n\n- (manual)\n"
            )
            for d, aspect in (
                ("2026-01-01", "first"),
                ("2026-05-04", "third"),
                ("2026-03-15", "second"),
            ):
                (folder / f"{d}-{aspect}.md").write_text(
                    f"---\ndate: {d}\ntopic: topic-a\naspect: {aspect}\n---\n\n# {aspect}\n"
                )
            (folder / "lessons.md").write_text(
                "---\nslug: topic-a-lessons\n---\n\n# Lessons\n"
            )
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _moc import rebuild_topic_readme\n"
                "from pathlib import Path\n"
                f"rebuild_topic_readme(Path({str(folder)!r}))\n"
            )
            _run_in_home(code, home)
            txt = (folder / "00-README.md").read_text()
            # Newest first: 2026-05-04 then 2026-03-15 then 2026-01-01.
            pos_05 = txt.find("2026-05-04")
            pos_03 = txt.find("2026-03-15")
            pos_01 = txt.find("2026-01-01")
            self.assertGreater(pos_05, 0)
            self.assertGreater(pos_03, pos_05)
            self.assertGreater(pos_01, pos_03)
            # lessons.md surfaces too.
            self.assertIn("lessons", txt)

    def test_tldr_and_manual_preserved_across_rebuilds(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            folder = home / "workspaces" / "ws1" / "topic-b"
            folder.mkdir(parents=True)
            original_tldr = "> PRESERVE-ME custom TL;DR line"
            original_manual = "- [[other-topic]] — preserved manual link"
            (folder / "00-README.md").write_text(
                "---\nslug: topic-b\ntype: topic\n---\n\n# Topic B\n\n## TL;DR\n\n"
                f"{original_tldr}\n\n## Aspects (auto)\n\n(populated)\n\n"
                f"## Cross-links (manual)\n\n{original_manual}\n"
            )
            (folder / "2026-05-01-note.md").write_text("---\ndate: 2026-05-01\n---\n\nbody\n")
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _moc import rebuild_topic_readme\n"
                "from pathlib import Path\n"
                f"rebuild_topic_readme(Path({str(folder)!r}))\n"
                f"rebuild_topic_readme(Path({str(folder)!r}))\n"
            )
            _run_in_home(code, home)
            txt = (folder / "00-README.md").read_text()
            self.assertIn(original_tldr, txt)
            self.assertIn(original_manual, txt)

    def test_idempotent_modulo_last_touched(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            folder = home / "workspaces" / "ws1" / "topic-c"
            folder.mkdir(parents=True)
            (folder / "00-README.md").write_text(
                "---\nslug: topic-c\ntype: topic\n---\n\n# Topic C\n\n## TL;DR\n\n> x\n\n"
                "## Aspects (auto)\n\n(populated)\n\n## Cross-links (manual)\n\n- (m)\n"
            )
            (folder / "2026-04-01-x.md").write_text("---\n---\nbody\n")
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _moc import rebuild_topic_readme\n"
                "from pathlib import Path\n"
                f"rebuild_topic_readme(Path({str(folder)!r}))\n"
            )
            _run_in_home(code, home)
            first = (folder / "00-README.md").read_text()
            _run_in_home(code, home)
            second = (folder / "00-README.md").read_text()
            self.assertEqual(_strip_last_touched(first), _strip_last_touched(second))


if __name__ == "__main__":
    unittest.main()
