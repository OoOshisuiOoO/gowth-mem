"""v3 multi-aspect recall scoring tests.

Covers per-plan F16 layer_score buckets:
- 00-README.md inside topic folder      → 80
- lessons.md inside topic folder        → 75
- today's YYYY-MM-DD-<aspect>.md        → 90
- older YYYY-MM-DD-<aspect>.md          → 70
- v2.4 folder-note `<slug>/<slug>.md`   → 80 (legacy compat)
- other files inside topic folder       → 60
- workspace research/ subdir            → 65
- shared/ outside skills/               → 60
- shared/skills/                        → 40
"""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"


def _layer_score_in_home(home: Path, rel_path: str, ws: str = "ws1") -> int:
    env = os.environ.copy()
    env["GOWTH_MEM_HOME"] = str(home)
    code = (
        "import sys; sys.path.insert(0, 'hooks/scripts');\n"
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('r', 'hooks/scripts/recall-active.py')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "from pathlib import Path\n"
        f"p = Path({str(home / rel_path)!r})\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text('test\\n')\n"
        f"print('score=' + str(mod.layer_score(p, {ws!r})))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    )
    line = [l for l in out.stdout.splitlines() if l.startswith("score=")][0]
    return int(line.split("=", 1)[1])


class MultiAspectRecallV3Tests(unittest.TestCase):
    def test_today_aspect_outranks_older_aspect(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            today = date.today().isoformat()
            old = (date.today() - timedelta(days=30)).isoformat()
            s_today = _layer_score_in_home(home, f"workspaces/ws1/topic/{today}-x.md")
            s_old = _layer_score_in_home(home, f"workspaces/ws1/topic/{old}-x.md")
            self.assertEqual(s_today, 90)
            self.assertEqual(s_old, 70)
            self.assertGreater(s_today, s_old)

    def test_readme_and_lessons_buckets(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            s_readme = _layer_score_in_home(home, "workspaces/ws1/topic/00-README.md")
            s_lessons = _layer_score_in_home(home, "workspaces/ws1/topic/lessons.md")
            self.assertEqual(s_readme, 80)
            self.assertEqual(s_lessons, 75)

    def test_v24_folder_note_legacy_bucket(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            # v2.4: parent.name == filename stem
            s = _layer_score_in_home(home, "workspaces/ws1/legacy/legacy.md")
            self.assertEqual(s, 80)

    def test_research_subdir_bucket(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            s = _layer_score_in_home(home, "workspaces/ws1/research/deepdive.md")
            self.assertEqual(s, 65)

    def test_shared_skills_lower_than_shared_other(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            s_skill = _layer_score_in_home(home, "shared/skills/foo.md")
            s_other = _layer_score_in_home(home, "shared/tools.md")
            self.assertEqual(s_skill, 40)
            self.assertEqual(s_other, 60)


if __name__ == "__main__":
    unittest.main()
