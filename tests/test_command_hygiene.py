"""Guards on commands/*.md and skills/*/SKILL.md (v4.3).

Two classes of defect these catch, both found in the Zero-Mem audit:

1. Dead-layout drift (D9). Six commands and nine skills still targeted the
   pre-v2.7 per-project layout (`$CLAUDE_PROJECT_DIR`/`$PWD` + `docs/...`).
   `/mem-journal` therefore WROTE memory to `$PWD/docs/journal/` — outside the
   vault, so never git-synced, never indexed, invisible to every hook — and
   `/mem-cost` measured 0 of 9 files, which is why the bootstrap regression that
   dropped `docs/handoff.md` from every session went unnoticed.

2. Frontmatter that mis-parses. A bare `: ` inside an unquoted YAML value makes the
   description parse wrong, which is a real cause of "skill không rõ" — three
   commands shipped broken this way before v3.6.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMANDS = sorted((ROOT / "commands").glob("mem-*.md"))
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))

# `$PWD`/`$CLAUDE_PROJECT_DIR` are legitimate when talking about the CODE repo
# (e.g. plugin-repo-cwd checks); they are only a defect when used to locate MEMORY.
MEMORY_PATH_RE = re.compile(
    r"(?:\$CLAUDE_PROJECT_DIR|\$PWD|\$\{CLAUDE_PROJECT_DIR[^}]*\})[\"']?/?docs/")
DOCS_JOURNAL_RE = re.compile(r"(?<!\$PWD/)docs/journal")

# Prose that documents the OLD broken behaviour is deliberate and must survive —
# explaining why a path changed is how the next reader avoids reintroducing it.
HISTORICAL_RE = re.compile(r"Before v4\.3|pre-v2\.7|used to|previously|no longer", re.I)


def _offending_lines(path: pathlib.Path, rx: re.Pattern) -> list[str]:
    """Lines matching *rx*, excluding lines explicitly describing past behaviour."""
    out = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if rx.search(line) and not HISTORICAL_RE.search(line):
            out.append(f"{path.name}:{i}")
    return out


class DeadLayoutTest(unittest.TestCase):
    def test_no_command_locates_memory_under_pwd(self):
        bad = [x for p in COMMANDS for x in _offending_lines(p, MEMORY_PATH_RE)]
        self.assertEqual(bad, [], f"commands writing/reading memory under $PWD: {bad}")

    def test_no_skill_locates_memory_under_pwd(self):
        bad = [x for p in SKILLS for x in _offending_lines(p, MEMORY_PATH_RE)]
        self.assertEqual(bad, [], f"skills locating memory under $PWD: {bad}")

    def test_journal_is_not_described_under_docs(self):
        """v3 layout: journal/ is workspace-level, docs/ holds handoff|exp|ref|tools|files."""
        offenders = [x for p in COMMANDS + SKILLS
                     for x in _offending_lines(p, DOCS_JOURNAL_RE)]
        self.assertEqual(offenders, [],
                         f"`docs/journal` is not a real path: {offenders}")


class FrontmatterTest(unittest.TestCase):
    def test_every_command_has_parseable_description(self):
        broken = []
        for p in COMMANDS:
            lines = p.read_text().split("\n")
            if lines[0].strip() != "---":
                broken.append(f"{p.name}: no frontmatter")
                continue
            desc = next((l for l in lines[1:10] if l.startswith("description:")), None)
            if desc is None:
                broken.append(f"{p.name}: no description")
                continue
            value = desc[len("description:"):].strip()
            quoted = (value.startswith('"') and value.endswith('"')) or \
                     (value.startswith("'") and value.endswith("'"))
            if not quoted and ": " in value:
                broken.append(f"{p.name}: unquoted ': ' mis-parses")
        self.assertEqual(broken, [], f"broken frontmatter: {broken}")

    def test_every_skill_has_parseable_description(self):
        broken = []
        for p in SKILLS:
            lines = p.read_text().split("\n")
            desc = next((l for l in lines[1:10] if l.startswith("description:")), None)
            if desc is None:
                broken.append(f"{p.relative_to(ROOT)}: no description")
                continue
            value = desc[len("description:"):].strip()
            quoted = (value.startswith('"') and value.endswith('"')) or \
                     (value.startswith("'") and value.endswith("'"))
            if not quoted and ": " in value:
                broken.append(f"{p.relative_to(ROOT)}: unquoted ': ' mis-parses")
        self.assertEqual(broken, [], f"broken frontmatter: {broken}")


class StaleClaimTest(unittest.TestCase):
    def test_journal_is_not_called_permanent_or_immutable(self):
        """Since v3.6 the journal is the EPHEMERAL capture buffer with a raw TTL —
        docs promising permanence teach the model to leave data where _forget.py
        will archive it."""
        offenders = []
        for p in COMMANDS + SKILLS:
            text = p.read_text()
            for phrase in ("immutable raw log", "permanent log", "never prune`journal"):
                if phrase in text:
                    offenders.append(f"{p.relative_to(ROOT)}: '{phrase}'")
        self.assertEqual(offenders, [], f"stale permanence claims: {offenders}")


if __name__ == "__main__":
    unittest.main()
