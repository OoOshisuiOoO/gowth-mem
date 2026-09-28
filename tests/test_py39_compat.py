"""v4.7.6 — the runtime path and the suite must keep working on Python 3.9.

`tests/test_tags.py` annotated a helper `extra_env: dict | None` without
`from __future__ import annotations`: on 3.9 the annotation is evaluated at
`def` time, raises TypeError, and the WHOLE module fails to import — its 32
tests silently stopped running on 3.9 while every 3.14 run stayed green
(CI used `python-version: '3.x'`). CI now also runs 3.9; this test is the
fast local guard that works on any interpreter:

  1. every file parses with the 3.9 grammar (`ast.parse(feature_version=)`
     rejects `match`, PEP 695 type params, … — best effort, the CI 3.9 job is
     the complete check);
  2. no PEP 604 union (`X | Y`) in an annotation Python evaluates at runtime —
     function signatures, module- and class-level variable annotations —
     unless the module has `from __future__ import annotations`.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = sorted(
    list((ROOT / "hooks" / "scripts").glob("*.py"))
    + list((ROOT / "tests").glob("*.py"))
    + list((ROOT / "bin").glob("*.py"))
)


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in tree.body
    )


def _has_union(node: ast.AST | None) -> bool:
    return node is not None and any(
        isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(node)
    )


def runtime_pep604_lines(tree: ast.Module) -> list[int]:
    """Lines of `X | Y` annotations that 3.9 evaluates (and chokes on) at runtime."""
    if _has_future_annotations(tree):
        return []
    bad: list[int] = []

    def visit(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = child.args
                params = a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]
                for p in params:
                    if _has_union(p.annotation):
                        bad.append(p.lineno)
                if _has_union(child.returns):
                    bad.append(child.lineno)
                visit(child, True)
            elif isinstance(child, ast.AnnAssign):
                # Local annotations inside a function body are never evaluated.
                if not in_function and _has_union(child.annotation):
                    bad.append(child.lineno)
            elif isinstance(child, ast.ClassDef):
                # A class body runs (and evaluates its annotations) when the
                # class is created — also a class defined inside a function.
                visit(child, False)
            else:
                visit(child, in_function)

    visit(tree, False)
    return sorted(set(bad))


class Py39CompatTests(unittest.TestCase):
    def test_targets_were_found(self):
        self.assertGreater(len(TARGETS), 50)

    def test_detector_flags_the_v4_7_5_regression(self):
        # Positive signal: the exact shape that broke test_tags.py is caught...
        src = "import os\n\ndef _run(code: str, extra_env: dict | None = None) -> None:\n    pass\n"
        self.assertEqual(runtime_pep604_lines(ast.parse(src)), [3])
        # ...while the forms 3.9 accepts are not.
        ok = ("from __future__ import annotations\n\ndef f(x: dict | None) -> int | None: pass\n")
        self.assertEqual(runtime_pep604_lines(ast.parse(ok)), [])
        local = "def f():\n    x: int | None = None\n    return x\n"
        self.assertEqual(runtime_pep604_lines(ast.parse(local)), [])
        cls = "class C:\n    x: int | None = None\n"
        self.assertEqual(runtime_pep604_lines(ast.parse(cls)), [2])
        # A class defined inside a function still evaluates its annotations
        # (review finding: calling make() raises TypeError on 3.9).
        nested = "def make():\n    class Handler:\n        timeout: float | None = None\n    return Handler\n"
        self.assertEqual(runtime_pep604_lines(ast.parse(nested)), [3])

    def test_every_file_parses_with_the_3_9_grammar(self):
        for path in TARGETS:
            with self.subTest(path=path.relative_to(ROOT)):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path),
                          feature_version=(3, 9))

    def test_no_runtime_pep604_annotations(self):
        offenders = {}
        for path in TARGETS:
            lines = runtime_pep604_lines(ast.parse(path.read_text(encoding="utf-8")))
            if lines:
                offenders[str(path.relative_to(ROOT))] = lines
        self.assertEqual(offenders, {}, "add `from __future__ import annotations` "
                                        "or use typing.Optional/Union")


if __name__ == "__main__":
    unittest.main()
