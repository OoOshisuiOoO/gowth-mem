"""v3 wikilink resolution tests — `[[slug]]` must resolve across v3/v2.4/v2.3 layouts.

Per plan §2.7 / locked decision R9: read-path stays permissive during multi-machine
partial migration. The order is:
  1. v3 landing: <base>/<slug>/00-README.md
  2. v3 rglob:   any */00-README.md whose `slug:` frontmatter matches
  3. v2.4 landing: <base>/<slug>/<slug>.md (folder-note)
  4. v2.4 rglob:   any */<slug>.md folder-note (parent.name == slug)
  5. v2.3 flat: <base>/<slug>.md
  6. v2.3 rglob: any <slug>.md
"""
import os
import subprocess
import sys
import tempfile
import unittest
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


class WikilinkV3Tests(unittest.TestCase):
    def test_resolve_v3_landing_first(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            ws = home / "workspaces" / "ws1"
            (ws / "ema-cross").mkdir(parents=True)
            (ws / "ema-cross" / "00-README.md").write_text(
                "---\nslug: ema-cross\n---\n\n# EMA Cross\n"
            )
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _wikilink import resolve\n"
                "p = resolve('ema-cross', current_ws='ws1')\n"
                "assert p is not None and p.name == '00-README.md', p\n"
                "assert p.parent.name == 'ema-cross', p\n"
            )
            _run_in_home(code, home)

    def test_resolve_falls_back_to_v24_folder_note(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            ws = home / "workspaces" / "ws1"
            (ws / "legacy").mkdir(parents=True)
            # v2.4 folder-note layout: <slug>/<slug>.md (no 00-README.md)
            (ws / "legacy" / "legacy.md").write_text(
                "---\nslug: legacy\n---\n\n# Legacy v2.4 topic\n"
            )
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _wikilink import resolve\n"
                "p = resolve('legacy', current_ws='ws1')\n"
                "assert p is not None and p.name == 'legacy.md', p\n"
                "assert p.parent.name == 'legacy', p\n"
            )
            _run_in_home(code, home)

    def test_resolve_falls_back_to_v23_flat(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            ws = home / "workspaces" / "ws1"
            ws.mkdir(parents=True)
            # v2.3 flat layout: <base>/<slug>.md (no folder at all)
            (ws / "old-flat.md").write_text("---\nslug: old-flat\n---\n\n# Old flat\n")
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _wikilink import resolve\n"
                "p = resolve('old-flat', current_ws='ws1')\n"
                "assert p is not None and p.name == 'old-flat.md', p\n"
            )
            _run_in_home(code, home)

    def test_v3_landing_wins_over_v24_when_both_present(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            ws = home / "workspaces" / "ws1"
            (ws / "dual").mkdir(parents=True)
            (ws / "dual" / "00-README.md").write_text(
                "---\nslug: dual\n---\n\n# v3 wins\n"
            )
            (ws / "dual" / "dual.md").write_text(
                "---\nslug: dual\n---\n\n# v2.4 loses\n"
            )
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _wikilink import resolve\n"
                "p = resolve('dual', current_ws='ws1')\n"
                "assert p is not None and p.name == '00-README.md', p\n"
            )
            _run_in_home(code, home)

    def test_resolve_returns_none_for_missing(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "workspaces" / "ws1").mkdir(parents=True)
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _wikilink import resolve\n"
                "p = resolve('does-not-exist', current_ws='ws1')\n"
                "assert p is None, p\n"
            )
            _run_in_home(code, home)


if __name__ == "__main__":
    unittest.main()
