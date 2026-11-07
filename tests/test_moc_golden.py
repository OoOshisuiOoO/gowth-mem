"""v4.9 (§2.3): a card-less topic README renders byte-identically to v4.8.

The topic-card work changed `_moc.rebuild_topic_readme` (card-aware TL;DR,
unknown frontmatter keys kept, scalar list keys no longer split into
characters). Those changes must not move a single byte of a README that has
no card, no unknown key and no scalar list value — every vault on disk is
made of those, and a byte moved on every machine is a sync conflict on every
machine.

`tests/fixtures/moc_v48_golden.json` holds what the v4.8.0 code (`cfe939f`)
rendered for the fixtures built below — generated once from that code and
never regenerated from the code under test. To extend it, render the new
fixture with v4.8.0's `_moc.py` (`git show cfe939f:hooks/scripts/_moc.py` into
a copy of hooks/scripts, then `render_all`), never with the current one.
Today's date (`last_touched`, `last_rebuilt`, a defaulted `created`) is
normalised to `@TODAY@`. The same comparison over a copy of the live vault
(136 topic READMEs, v4.8 vs v4.9 rebuild) found no differing byte.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
GOLDEN = ROOT / "tests" / "fixtures" / "moc_v48_golden.json"
WS = "gold"

# Topic folders rebuilt by the golden run, in order (vault-relative to the ws).
TOPICS = ("fresh", "skeleton", "curated", "no-tldr", "no-manual", "no-frontmatter",
          "empty-lists", "trailing-blanks", "domain/nested")


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def build_fixtures(home: Path) -> Path:
    """A workspace of card-less topic folders covering every branch of the
    v4.8 rebuild that a real vault exercises. No unknown frontmatter keys and
    no scalar list values (both deliberately render differently in v4.9)."""
    sys.path.insert(0, str(SCRIPTS))
    from _topic_templates import render  # type: ignore

    wsd = home / "workspaces" / WS
    _w(wsd / "workspace.json", '{"name": "gold"}')

    # 1. no README at all: a legacy folder note makes it a topic folder.
    _w(wsd / "fresh" / "fresh.md", "# Fresh\n\nLegacy folder note body line.\n")
    _w(wsd / "fresh" / "2025-02-01-first-look.md",
       "---\nslug: fresh-first-look\ndate: 2025-02-01\n---\n\n# First look\n\n"
       "- [exp] the first look found the cache cold because nothing warmed it\n")
    _w(wsd / "fresh" / "lessons.md", "# Lessons & Troubleshooting\n\n## Entries\n\n")

    # 2. the pristine skeleton `ensure_topic_folder` writes, plus aspects.
    _w(wsd / "skeleton" / "00-README.md",
       render("misc", "skeleton", "Skeleton", "2025-03-01", [], ""))
    _w(wsd / "skeleton" / "2025-03-02-alpha.md",
       "---\nslug: skeleton-alpha\n---\n\n# Alpha\n\n> 1-2 lines describing what this aspect captures.\n\n"
       "(empty)\n\n- [ref] alpha uses port 8443 Source: runbook.md\n")
    _w(wsd / "skeleton" / "2025-03-05-beta-gamma.md",
       "## [decision] beta over gamma\nbeta wins because gamma leaks file handles\n")
    _w(wsd / "skeleton" / "2025-03-05-alpha.md", "plain first line without frontmatter\n")

    # 3. a curated README: every known key set, custom TL;DR + manual block.
    _w(wsd / "curated" / "00-README.md",
       "---\nslug: curated\ntitle: Curated Topic\ntype: runbook\nstatus: active\n"
       "maturity: stable\ncreated: 2025-01-10\nlast_touched: 2025-04-01\n"
       "parents: [infra]\nlinks: [skeleton, fresh]\naliases: [cur, curation]\n"
       "tags: [ops, pager]\n---\n\n# Curated Topic\n\n## TL;DR\n\n"
       "> Pager runbook for the curated service.\n> Second quoted line.\n\n"
       "Free text the user wrote under the TL;DR.\n\n"
       "## Aspects (auto)\n\n(stale list)\n\n## Cross-links (manual)\n\n"
       "- [[skeleton]] — sibling\n- [[fresh|fresh topic]]\n\nTrailing manual prose.\n")
    _w(wsd / "curated" / "2025-04-01-pager-drill.md",
       "---\nslug: curated-pager-drill\ntitle: Pager drill\ntype: aspect\n---\n\n"
       "# Pager drill\n\n## Context\n\n- [exp] the pager drill paged twice because dedup was off\n")
    _w(wsd / "curated" / "lessons.md", "# Lessons\n\n## Entries\n\n## [2025-04-02] x\n")
    _w(wsd / "curated" / "old-notes.md", "---\nslug: x\n---\n\n# Old notes\n\nLegacy undated aspect.\n")

    # 4. a hand-written README with no TL;DR (v4.8 inserts the TODO one).
    _w(wsd / "no-tldr" / "00-README.md",
       "---\nslug: no-tldr\ntitle: No TLDR\ntype: concept\nstatus: draft\n---\n\n"
       "# No TLDR\n\nIntro paragraph.\n\n## Notes\n\nsome notes\n\n"
       "## Cross-links (manual)\n\n- [[curated]]\n")

    # 5. TL;DR but no manual block (v4.8 adds the default one).
    _w(wsd / "no-manual" / "00-README.md",
       "---\nslug: no-manual\ntitle: No Manual\ntype: misc\nstatus: draft\ncreated: 2025-05-01\n---\n\n"
       "# No Manual\n\n## TL;DR\n\n> Only a TL;DR here.\n")
    _w(wsd / "no-manual" / "2025-05-02-solo.md", "---\n---\n\n# Solo\n\nsolo preview text\n")

    # 6. no frontmatter at all.
    _w(wsd / "no-frontmatter" / "00-README.md",
       "# Bare\n\n## TL;DR\n\n> bare summary\n\n## Aspects (auto)\n\n(x)\n\n"
       "## Cross-links (manual)\n\n- none\n")

    # 7. empty list values in every list form v4.8 renders as [].
    _w(wsd / "empty-lists" / "00-README.md",
       "---\nslug: empty-lists\ntitle: Empty Lists\ntype: misc\nstatus: draft\ncreated: 2025-06-01\n"
       "parents: []\nlinks:\naliases: []\ntags: \n---\n\n# Empty Lists\n\n## TL;DR\n\n> e\n")

    # 8. a TL;DR followed by runs of blank lines (normalised by the rebuild).
    _w(wsd / "trailing-blanks" / "00-README.md",
       "---\nslug: trailing-blanks\ntitle: Trailing Blanks\ntype: misc\nstatus: draft\n"
       "created: 2025-07-01\n---\n\n# Trailing Blanks\n\n## TL;DR\n\n> t\n\n\n\n\n"
       "## Aspects (auto)\n\n\n(x)\n\n## Cross-links (manual)\n\n- keep\n\n\n")

    # 9. a nested topic under a domain folder.
    _w(wsd / "domain" / "nested" / "00-README.md",
       "---\nslug: nested\ntitle: Nested\ntype: tool\nstatus: draft\ncreated: 2025-08-01\n"
       "parents: [domain]\n---\n\n# Nested\n\n## TL;DR\n\n> nested summary\n")
    _w(wsd / "domain" / "nested" / "2025-08-02-deep.md", "---\n---\n# Deep\n(skip me)\nreal preview\n")

    # A legacy flat topic at the ws root (rendered in the workspace _MAP.md).
    _w(wsd / "flatlegacy.md", "---\nslug: flatlegacy\ntitle: Flat Legacy\nstatus: old\n---\n\n# Flat\n")
    return wsd


def render_all(home: Path) -> dict:
    """Run the rebuilds in a subprocess against `home`; return {name: text}."""
    code = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from pathlib import Path\n"
        "import _moc\n"
        "ws = Path(sys.argv[2])\n"
        "out = {}\n"
        "for t in json.loads(sys.argv[3]):\n"
        "    p = _moc.rebuild_topic_readme(ws / t)\n"
        "    out['topic:' + t] = p.read_text(encoding='utf-8')\n"
        "out['map'] = _moc.rebuild_workspace_moc(sys.argv[4]).read_text(encoding='utf-8')\n"
        "print(json.dumps(out))\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("GOWTH_WORKSPACE", "AI_AGENT", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF")}
    env["GOWTH_MEM_HOME"] = str(home)
    r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), str(home / "workspaces" / WS),
                        json.dumps(list(TOPICS)), WS],
                       env=env, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    today = date.today().isoformat()
    return {k: v.replace(today, "@TODAY@") for k, v in json.loads(r.stdout).items()}


class MocGoldenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_mocgold_")
        self.home = Path(self.tmp)
        build_fixtures(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_card_less_readmes_render_like_v48(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        got = render_all(self.home)
        # Positive signal: the run rendered every fixture and the map.
        self.assertEqual(sorted(got), sorted(golden))
        self.assertIn("## Aspects (auto)", got["topic:skeleton"])
        for name in sorted(golden):
            self.assertEqual(got[name], golden[name], f"{name} differs from the v4.8 render")

    def test_second_and_third_rebuild_change_nothing(self):
        first = render_all(self.home)
        before = {t: (self.home / "workspaces" / WS / t / "00-README.md").read_bytes() for t in TOPICS}
        render_all(self.home)
        render_all(self.home)
        after = {t: (self.home / "workspaces" / WS / t / "00-README.md").read_bytes() for t in TOPICS}
        self.assertEqual(before, after)
        self.assertIn("Pager runbook for the curated service.", first["topic:curated"])


if __name__ == "__main__":
    unittest.main()
