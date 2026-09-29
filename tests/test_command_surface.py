"""v4.8 Task 10: the command surface stays small and every reference resolves.

Audit: 39 `gowth-mem:mem-*` lines cost 9,153 chars (≈2,288 tokens) of skill
listing per session — 4× the bootstrap the model actually received. Rarely
typed operations live under `/mem-ops <sub>` (bodies in templates/ops/, which
Claude Code never lists); descriptions are short and YAML-safe.
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = sorted((ROOT / "commands").glob("*.md"))
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
OPS = ROOT / "templates" / "ops"
SCAN_DIRS = [ROOT / "commands", ROOT / "skills", ROOT / "templates", ROOT / "hooks", ROOT / "bin",
             ROOT / ".claude-plugin"]
SCAN_FILES = [ROOT / "README.md"]
TOKEN_RE = re.compile(r"/mem-[a-z0-9-]+")
OPS_SUB_RE = re.compile(r"/mem-ops\s+([a-z0-9-]+)")
RESEARCH_SUB_RE = re.compile(r"/mem-research\s+(start|distill|status)")


def _description(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("description:"):
            v = line[len("description:"):].strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            return v
    return ""


class CommandSurfaceTest(unittest.TestCase):
    def test_at_most_16_commands(self):
        names = [p.stem for p in COMMANDS]
        self.assertLessEqual(len(names), 16, names)
        for required in ("mem-ops", "mem-recall", "mem-save", "mem-review", "mem-sync", "mem-doctor",
                         "mem-setup", "mem-install", "mem-workspace", "mem-research", "mem-distill",
                         "mem-topic", "mem-lesson", "mem-goal", "mem-handoff", "mem-cost"):
            self.assertIn(required, names)

    def test_descriptions_short_and_yaml_safe(self):
        for p in COMMANDS + SKILLS:
            d = _description(p)
            self.assertTrue(d, f"{p} has no description")
            self.assertLessEqual(len(d), 80, f"{p}: {len(d)} chars")
            self.assertNotIn(": ", d, f"{p}: bare ': ' inside a YAML value")
            self.assertNotIn(" #", d, f"{p}: ' #' is a YAML comment")

    def test_at_most_7_skills(self):
        self.assertLessEqual(len(SKILLS), 7, [p.parent.name for p in SKILLS])

    def test_ops_bodies_match_the_dispatcher_table(self):
        table = set(re.findall(r"^\| ([a-z0-9-]+) \|", (ROOT / "commands" / "mem-ops.md").read_text(), re.M))
        bodies = {p.stem for p in OPS.glob("*.md") if not p.stem.startswith("research-")}
        self.assertTrue(table, "mem-ops.md must carry a | sub | table")
        self.assertEqual(table, bodies)

    def test_every_reference_resolves(self):
        existing = {p.stem for p in COMMANDS}
        subs = {p.stem for p in OPS.glob("*.md")}
        files = list(SCAN_FILES)
        for d in SCAN_DIRS:
            files += [p for p in d.rglob("*") if p.suffix in (".md", ".py", ".sh", ".json") and "__pycache__" not in p.parts]
        dangling = []
        for f in files:
            text = f.read_text(encoding="utf-8", errors="ignore")
            for m in TOKEN_RE.finditer(text):
                name = m.group(0)[1:]
                if name in existing:
                    continue
                dangling.append(f"{f.relative_to(ROOT)}: /{name}")
            for m in OPS_SUB_RE.finditer(text):
                if m.group(1) not in subs:
                    dangling.append(f"{f.relative_to(ROOT)}: /mem-ops {m.group(1)} (no templates/ops/{m.group(1)}.md)")
            for m in RESEARCH_SUB_RE.finditer(text):
                if f"research-{m.group(1)}" not in subs:
                    dangling.append(f"{f.relative_to(ROOT)}: /mem-research {m.group(1)}")
        self.assertEqual(dangling, [], "\n".join(dangling))


class OpsDispatchTest(unittest.TestCase):
    """Verified on the real binary (claude 2.1.283, --plugin-dir): Claude Code
    substitutes ${CLAUDE_PLUGIN_ROOT} only inside COMMAND files. An ops body
    read at runtime from templates/ops/ keeps the literal expansion, and the
    Bash tool does not export the variable, so `python3 "${CLAUDE_PLUGIN_ROOT}/…"`
    ran as `python3 "/…"` and the model had to guess the path. Every command
    that dispatches to templates/ops/ must therefore hand the model the
    substituted root as an env prefix it can copy."""

    def test_dispatching_commands_carry_the_plugin_root_prefix(self):
        for p in COMMANDS:
            text = p.read_text()
            if "templates/ops/" not in text:
                continue
            self.assertIn("export CLAUDE_PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'", text,
                          f"{p.name}: dispatches to templates/ops/ without the export instruction")
            self.assertNotIn("prefix `CLAUDE_PLUGIN_ROOT=", text,
                             f"{p.name}: a prefix assignment does not apply to expansions in the same command (round-5 M-a)")
            self.assertIn("never inside the ops file", text, f"{p.name}: must explain the substitution gap")

    def test_the_prescribed_shell_form_actually_expands(self):
        """Round-5 M-a: `VAR=x cmd "${VAR}/y"` expands ${VAR} BEFORE the temporary
        assignment, so the first instruction ran `python3 "/hooks/…"`. The
        prescribed form is `export VAR=…; cmd "${VAR}/y"` in the same call."""
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
        prefix = subprocess.run(["bash", "-c", "CLAUDE_PLUGIN_ROOT='/x' echo \"${CLAUDE_PLUGIN_ROOT}/y\""],
                                capture_output=True, text=True, env=env).stdout.strip()
        export = subprocess.run(["bash", "-c", "export CLAUDE_PLUGIN_ROOT='/x'; echo \"${CLAUDE_PLUGIN_ROOT}/y\""],
                                capture_output=True, text=True, env=env).stdout.strip()
        self.assertEqual(prefix, "/y", "sanity: the prefix form must NOT expand (that is the bug)")
        self.assertEqual(export, "/x/y")


if __name__ == "__main__":
    unittest.main()
