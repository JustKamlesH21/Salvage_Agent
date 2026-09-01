#!/usr/bin/env python3
"""Architectural boundary check. Two rules, both mechanical.

RULE 1 — the Razorpay SDK is imported in exactly one file.
    Only api/executor/client.py may `import razorpay`. Everything else goes
    through execute_action(). CLAUDE.md invariant 1, SPEC §5.6, FR-08.

RULE 2 — `events` and `audit` are append-only.
    No update_one / delete_one / replace_one / find_one_and_update / drop and
    friends may target those collections, and their repository classes may not
    define a mutating method. PRD §8.4 layer 1, CLAUDE.md invariant 6.

What this check can and cannot see
---------------------------------
It is an AST walk, not a type checker. Rule 2 matches on the *text of the
receiver expression*: `db.events.update_one(...)`, `db["audit"].delete_one(...)`
and `self._events_coll.update_one(...)` are all caught, because "events" or
"audit" appears in the receiver. A collection reached through a variable that
mentions neither name — `c = db[name]; c.update_one(...)` — is not caught.
That residual hole is closed by the second half of rule 2: the append-only
repositories are the only sanctioned way to reach those collections, and this
check fails if they grow a mutating method.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Directories that hold no first-party Python worth scanning.
NOT_SOURCE = frozenset(
    {".venv", "venv", "node_modules", "__pycache__", "web", "htmlcov", "dist", "build"}
)

# Rule 1
SDK_MODULE = "razorpay"
SDK_ALLOWED = {ROOT / "api" / "executor" / "client.py"}
# Repo-wide, with no exemptions. CLAUDE.md invariant 1 says "no exceptions" and
# means it: scripts/seed_razorpay.py reaches Razorpay through
# api.executor.client.get_razorpay_client(), not through its own import.
#
# This list was `[ROOT / "api"]` on day 0, with scripts/ carved out. That
# exemption survived a green `make verify` because the harness was the
# exemption — see the note in NOTES.md. test_boundaries_check.py now asserts
# this list covers every top-level Python directory, so a new one cannot
# silently escape the scan.
SDK_SCAN_ROOTS = [ROOT / "api", ROOT / "scripts", ROOT / "sim", ROOT / "eval", ROOT / "tests"]

# Rule 2
APPEND_ONLY_COLLECTIONS = ("events", "audit")
MUTATING_CALLS = frozenset(
    {
        "update_one",
        "update_many",
        "delete_one",
        "delete_many",
        "replace_one",
        "find_one_and_update",
        "find_one_and_replace",
        "find_one_and_delete",
        "bulk_write",
        "drop",
        "rename",
    }
)
# Repository classes that must never gain a mutating method.
APPEND_ONLY_REPOS = frozenset({"_ChainedAppendOnly", "EventRepository", "AuditRepository"})
FORBIDDEN_REPO_METHOD_PREFIXES = ("update", "delete", "replace", "upsert", "remove", "drop", "set_")
APPEND_ONLY_SCAN_ROOTS = list(SDK_SCAN_ROOTS)


def top_level_python_dirs() -> list[Path]:
    """Every top-level directory in the repo that contains Python source.

    SDK_SCAN_ROOTS must cover all of these. A new package added next to api/
    would otherwise be outside the scan and free to import the SDK, which is
    precisely how the day-0 exemption went unnoticed.
    """
    found: list[Path] = []
    for child in sorted(ROOT.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in NOT_SOURCE:
            continue
        if child.name.endswith(".egg-info"):
            continue
        if any(child.rglob("*.py")):
            found.append(child)
    return found


class Violation:
    def __init__(self, path: Path, line: int, rule: str, detail: str) -> None:
        self.path = path
        self.line = line
        self.rule = rule
        self.detail = detail

    def __str__(self) -> str:
        try:
            location = self.path.relative_to(ROOT)
        except ValueError:
            # A path outside the repo — a test fixture, or the repo moved.
            location = self.path
        return f"  {location}:{self.line}  [{self.rule}] {self.detail}"


def python_files(roots: list[Path]) -> list[Path]:
    seen: dict[Path, None] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part in {".venv", "venv", "__pycache__", "node_modules"} for part in path.parts):
                continue
            seen[path] = None
    return list(seen)


def receiver_text(node: ast.Attribute, source: str) -> str:
    segment = ast.get_source_segment(source, node.value)
    return (segment or ast.dump(node.value)).lower()


def check_sdk_imports(path: Path, tree: ast.AST) -> list[Violation]:
    if path in SDK_ALLOWED:
        return []
    found: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == SDK_MODULE or alias.name.startswith(f"{SDK_MODULE}."):
                    found.append(
                        Violation(
                            path,
                            node.lineno,
                            "SDK-BOUNDARY",
                            f"imports `{alias.name}`. Only api/executor/client.py may import the "
                            f"Razorpay SDK — call execute_action() instead.",
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == SDK_MODULE or module.startswith(f"{SDK_MODULE}."):
                found.append(
                    Violation(
                        path,
                        node.lineno,
                        "SDK-BOUNDARY",
                        f"imports from `{module}`. Only api/executor/client.py may import the "
                        f"Razorpay SDK — call execute_action() instead.",
                    )
                )
        elif isinstance(node, ast.Call):
            # importlib.import_module("razorpay") and __import__("razorpay")
            target = node.func
            name = (
                target.attr
                if isinstance(target, ast.Attribute)
                else target.id
                if isinstance(target, ast.Name)
                else ""
            )
            if name in {"import_module", "__import__"} and node.args:
                first = node.args[0]
                if (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and first.value.split(".")[0] == SDK_MODULE
                ):
                    found.append(
                        Violation(
                            path,
                            node.lineno,
                            "SDK-BOUNDARY",
                            f"dynamically imports `{first.value}`. The boundary is not a "
                            f"style rule — route through execute_action().",
                        )
                    )
    return found


def check_append_only(path: Path, tree: ast.AST, source: str) -> list[Violation]:
    found: list[Violation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method not in MUTATING_CALLS:
                continue
            receiver = receiver_text(node.func, source)
            for name in APPEND_ONLY_COLLECTIONS:
                if name in receiver:
                    found.append(
                        Violation(
                            path,
                            node.lineno,
                            "APPEND-ONLY",
                            f"calls `{method}()` on `{receiver}`. The {name} collection is "
                            f"append-only (PRD §8.4) — insert a correcting document instead "
                            f"of editing history.",
                        )
                    )
                    break

        if isinstance(node, ast.ClassDef) and node.name in APPEND_ONLY_REPOS:
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                bare = member.name.lstrip("_")
                if bare.startswith(FORBIDDEN_REPO_METHOD_PREFIXES):
                    found.append(
                        Violation(
                            path,
                            member.lineno,
                            "APPEND-ONLY",
                            f"`{node.name}.{member.name}()` looks like a mutating method on an "
                            f"append-only repository. These repositories expose insert and "
                            f"reads only — delete the method.",
                        )
                    )
    return found


def main() -> int:
    violations: list[Violation] = []
    scanned = 0

    for path in python_files(SDK_SCAN_ROOTS):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        violations += check_sdk_imports(path, tree)
        scanned += 1

    for path in python_files(APPEND_ONLY_SCAN_ROOTS):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        violations += check_append_only(path, tree, source)

    missing_door = [p for p in SDK_ALLOWED if not p.exists()]
    for path in missing_door:
        violations.append(
            Violation(
                path,
                0,
                "SDK-BOUNDARY",
                "the single sanctioned Razorpay caller does not exist. Either create it or "
                "remove it from SDK_ALLOWED — an unenforced allowlist is worse than none.",
            )
        )

    print(f"boundaries: scanned {scanned} file(s) across {len(SDK_SCAN_ROOTS)} root(s)")
    if violations:
        print(f"boundaries: FAIL — {len(violations)} violation(s)")
        for violation in violations:
            print(violation)
        return 1
    print(
        "boundaries: OK — Razorpay SDK confined to api/executor/client.py; "
        "events and audit append-only"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
