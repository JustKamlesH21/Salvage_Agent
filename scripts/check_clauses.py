#!/usr/bin/env python3
"""Every gate cites its clause. Every policy rule explains itself.

GATES — each module in api/gates/ must declare module-level `CLAUSE_REF` and
`RULE_TEXT`. PRD §9, CLAUDE.md invariant 7. The eleven gates G0..G10 are named
in PRD §9; this script knows that list, so a missing gate is reported as
missing rather than passing silently because nobody wrote it yet.

RULES — every rule in api/policy/rules/*.yaml must carry `id`, `because` and
`cites`, both non-empty. SPEC §5.4, FR-06. These strings render in the UI: a
judge clicking any decision sees why, and on whose authority.

Read by AST and by YAML parse. Nothing is imported, so a gate with a broken
dependency still gets checked.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GATES_DIR = ROOT / "api" / "gates"
RULES_DIR = ROOT / "api" / "policy" / "rules"

REQUIRED_GATE_CONSTANTS = ("CLAUSE_REF", "RULE_TEXT")

# PRD §9. Order matters: G0_KILL is evaluated first, always (invariant 4).
EXPECTED_GATES: list[tuple[str, str]] = [
    ("G0_KILL", "g0_kill.py"),
    ("G1_WINDOW", "g1_window.py"),
    ("G2_PREDEBIT", "g2_predebit.py"),
    ("G3_AFA", "g3_afa.py"),
    ("G4_BUDGET", "g4_budget.py"),
    ("G5_COOLDOWN", "g5_cooldown.py"),
    ("G6_OPTOUT", "g6_optout.py"),
    ("G7_THIRDPARTY", "g7_thirdparty.py"),
    ("G8_POSTDEBIT", "g8_postdebit.py"),
    ("G9_HALTED", "g9_halted.py"),
    ("G10_IDEMPOTENT", "g10_idempotent.py"),
]

# Modules in api/gates/ that are infrastructure, not gates, and so are exempt.
NON_GATE_MODULES = frozenset({"__init__.py", "base.py", "chain.py", "types.py", "registry.py"})

REQUIRED_RULE_FIELDS = ("id", "because", "cites")


def module_constants(path: Path) -> set[str]:
    """Module-level names assigned in a file, without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            names.add(node.target.id)
    return names


def check_gates() -> list[str]:
    problems: list[str] = []

    if not GATES_DIR.exists():
        return [f"api/gates/ does not exist. All {len(EXPECTED_GATES)} gates are unwritten."]

    present = {p.name for p in GATES_DIR.glob("*.py")} - NON_GATE_MODULES
    checked = 0

    for gate_name, filename in EXPECTED_GATES:
        path = GATES_DIR / filename
        if not path.exists():
            problems.append(f"{gate_name}: api/gates/{filename} does not exist")
            continue
        present.discard(filename)
        checked += 1
        constants = module_constants(path)
        for required in REQUIRED_GATE_CONSTANTS:
            if required not in constants:
                problems.append(
                    f"{gate_name}: api/gates/{filename} does not declare {required}. "
                    f"Every gate names the clause it enforces and quotes it."
                )

    # A gate module nobody declared is as bad as a missing one: it is a rule
    # being enforced that no one wrote down.
    for orphan in sorted(present):
        constants = module_constants(GATES_DIR / orphan)
        problems.append(
            f"api/gates/{orphan} is not in EXPECTED_GATES. Either add it to PRD §9 and to "
            f"this script's list, or move it out of api/gates/. "
            f"(declares: {', '.join(sorted(constants)) or 'nothing'})"
        )

    print(f"clauses: {checked}/{len(EXPECTED_GATES)} gate module(s) found in api/gates/")
    return problems


def check_rules() -> list[str]:
    problems: list[str] = []

    if not RULES_DIR.exists():
        return ["api/policy/rules/ does not exist. The policy engine has no rules."]

    files = sorted(RULES_DIR.glob("*.yaml")) + sorted(RULES_DIR.glob("*.yml"))
    if not files:
        print("clauses: 0 policy rule file(s) found in api/policy/rules/")
        return [
            "api/policy/rules/ contains no YAML. The policy engine is what decides the action; "
            "an empty rulebook means nothing is decided deterministically."
        ]

    total_rules = 0
    for path in files:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        rules = loaded.get("rules", []) if isinstance(loaded, dict) else loaded
        if not isinstance(rules, list):
            problems.append(f"{path.name}: expected a list of rules, or a `rules:` key holding one")
            continue
        for index, rule in enumerate(rules):
            total_rules += 1
            label = f"{path.name}[{index}]"
            if not isinstance(rule, dict):
                problems.append(f"{label}: rule is not a mapping")
                continue
            label = f"{path.name}:{rule.get('id', f'<no id, index {index}>')}"
            for field in REQUIRED_RULE_FIELDS:
                value = rule.get(field)
                if value is None or (isinstance(value, (str, list)) and len(value) == 0):
                    problems.append(
                        f"{label}: missing or empty `{field}`. Every rule states why it fires "
                        f"and on whose authority — those strings render in the UI."
                    )

    print(f"clauses: {total_rules} policy rule(s) across {len(files)} file(s)")
    return problems


def main() -> int:
    problems = check_gates() + check_rules()
    if problems:
        print(f"clauses: FAIL — {len(problems)} problem(s)")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("clauses: OK — every gate cites a clause, every rule explains itself")
    return 0


if __name__ == "__main__":
    sys.exit(main())
