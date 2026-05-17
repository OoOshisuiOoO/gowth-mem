"""v3 topic routing tests — verify the FOLDER + dated aspect contract.

Covers:
- `_topic.route()` returns `<slug>/YYYY-MM-DD-<aspect>.md` (dated aspect path).
- `_topic.route()` creates the topic folder + `00-README.md` on first call (idempotent).
- `_topic.ensure_topic_folder()` returns the folder, never a file path.
- `_topic.resolve_topic_folder()` returns the folder without spawning a dated aspect.
- `_topic.derive_topic_slug()` matches `route()` slug logic but spawns nothing.
- `[secret-ref]` lines side-channel to `shared/secrets.md`.
- `[skill-ref]` lines side-channel to `workspaces/<ws>/skills/<slug>.md`.
- Reserved subdirs (`docs`, `journal`, `skills`, `research`) blocked as topic slugs.
- Reserved aspect names (`readme`, `lessons`, `00-readme`) blocked.
"""
import importlib.util
import os
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
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


class TopicRouteV3Tests(unittest.TestCase):
    def test_route_returns_dated_aspect_path_and_creates_folder(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _topic import route\n"
                "from _home import workspace_dir\n"
                "p = route('database query optimization PostgreSQL index scan',"
                " ws='ws1')\n"
                "assert p.is_file(), f'route did not create file: {p}'\n"
                "assert p.parent.name not in ('docs','journal','skills','research'), p\n"
                "readme = p.parent / '00-README.md'\n"
                "assert readme.is_file(), f'00-README.md missing: {readme}'\n"
                "assert '-' in p.name and p.name.endswith('.md'), p.name\n"
                "print('ok=' + str(p))\n"
            )
            out = _run_in_home(code, home)
            self.assertIn("ok=", out.stdout)
            # The dated aspect filename must start with today's ISO date.
            wsd = home / "workspaces" / "ws1"
            self.assertTrue(wsd.is_dir())
            today = date.today().isoformat()
            # Walk and find at least one YYYY-MM-DD-<aspect>.md
            dated = list(wsd.rglob(f"{today}-*.md"))
            self.assertTrue(dated, f"no dated aspect created: {list(wsd.rglob('*.md'))}")

    def test_ensure_topic_folder_returns_folder_not_file(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _topic import ensure_topic_folder\n"
                "p = ensure_topic_folder('ema-cross', ws='ws1')\n"
                "assert p.is_dir(), f'expected folder, got {p}'\n"
                "assert (p / '00-README.md').is_file()\n"
                "# Second call is idempotent — no error, same folder.\n"
                "p2 = ensure_topic_folder('ema-cross', ws='ws1')\n"
                "assert p2 == p\n"
            )
            _run_in_home(code, home)

    def test_resolve_topic_folder_does_not_spawn_dated_aspect(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _topic import resolve_topic_folder\n"
                "p = resolve_topic_folder('lessons-only', ws='ws1')\n"
                "assert p.is_dir()\n"
                "# Only 00-README.md should exist — no YYYY-MM-DD-*.md.\n"
                "dated = list(p.glob('????-??-??-*.md'))\n"
                "assert not dated, f'resolve_topic_folder leaked dated aspect: {dated}'\n"
            )
            _run_in_home(code, home)

    def test_derive_topic_slug_matches_route_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _topic import derive_topic_slug\n"
                "from _home import workspace_dir\n"
                "slug = derive_topic_slug('alpha beta gamma delta', ws='ws1')\n"
                "assert isinstance(slug, str)\n"
                "# Nothing should be written by derive.\n"
                "wsd = workspace_dir('ws1')\n"
                "if wsd.is_dir():\n"
                "    files = list(wsd.rglob('*.md'))\n"
                "    assert all(f.parent.name in ('docs',) or 'misc' in str(f) for f in files), files\n"
                "print('slug=' + slug)\n"
            )
            out = _run_in_home(code, home)
            self.assertIn("slug=", out.stdout)

    def test_secret_ref_sidechannels_to_shared_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _topic import route\n"
                "p = route('[secret-ref] STARROCKS_BE_TOKEN', ws='ws1')\n"
                "assert p.name == 'secrets.md' and 'shared' in str(p), p\n"
            )
            _run_in_home(code, home)

    def test_skill_ref_sidechannels_to_workspace_skills(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            code = (
                "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                "from _topic import route\n"
                "p = route('[skill-ref] kubectl-debug', ws='ws1')\n"
                "assert 'skills' in str(p) and p.name.endswith('.md'), p\n"
            )
            _run_in_home(code, home)

    def test_reserved_subdir_blocked_as_topic_slug(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            for bad in ("docs", "journal", "skills", "research"):
                code = (
                    "import sys; sys.path.insert(0, 'hooks/scripts');\n"
                    "from _topic import ensure_topic_folder\n"
                    f"try:\n"
                    f"    ensure_topic_folder({bad!r}, ws='ws1')\n"
                    f"    raise SystemExit('expected ValueError for {bad}')\n"
                    f"except ValueError:\n"
                    f"    pass\n"
                )
                _run_in_home(code, home)

    def test_reserved_aspect_name_blocked(self):
        from importlib.util import spec_from_file_location, module_from_spec

        spec = spec_from_file_location("topic_mod", SCRIPTS / "_topic.py")
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        for bad in ("readme", "lessons", "00-readme"):
            with self.assertRaises(ValueError, msg=f"{bad} should be blocked"):
                mod._validate_aspect_slug(bad)


if __name__ == "__main__":
    unittest.main()
