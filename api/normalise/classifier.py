"""Pure lookup: (code, reason, description) -> FailureClass. No LLM, no I/O.

Phase 1 (NOTES.md day-1 close, finding 1) proved `error_reason`/`error_source`/
`error_step` are frequently null on real payloads — only `error_code` and
`error_description` survive. So the match keys actually used here are the
ones that carry real signal: `reason` (exact, when populated — the strongest
signal), `description_contains` (substring, for the null-reason case), and
`code` (alone, only for the two codes SPEC §4.1 names directly). `error_source`
and `error_step` informed which class each taxonomy entry was assigned to
while building taxonomy/failure_classes.yaml, but no rule keys on them at
match time — every real capture that had a populated `source`/`step` also had
a populated `reason`, so they never added a signal `reason` didn't already
carry. Kept out of the match function rather than added as unused parameters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from api.normalise.types import FailureClass, NormaliseResult, TaxonomyRule

TAXONOMY_PATH = Path(__file__).resolve().parent.parent.parent / "taxonomy" / "failure_classes.yaml"

_NO_MATCH_NOTE = "no taxonomy rule matched; classified UNKNOWN, never a guess (FR-04)"


def classify(
    rules: list[TaxonomyRule],
    *,
    code: str | None,
    reason: str | None,
    description: str | None,
) -> NormaliseResult:
    """First matching rule wins, same pattern the policy engine uses (SPEC
    §5.4), reused here for consistency. A rule matches when every match key it
    sets equals (or, for description_contains, is a substring of) the
    corresponding input; an unset key never excludes a candidate.
    """
    for rule in rules:
        if not _matches(rule, code=code, reason=reason, description=description):
            continue
        return NormaliseResult(failure_class=rule.failure_class, source=rule.source, note=rule.note)
    return NormaliseResult(failure_class=FailureClass.UNKNOWN, source=None, note=_NO_MATCH_NOTE)


def _matches(
    rule: TaxonomyRule, *, code: str | None, reason: str | None, description: str | None
) -> bool:
    reason_ok = rule.reason is None or (
        reason is not None and reason.strip().lower() == rule.reason.strip().lower()
    )
    code_ok = rule.code is None or (
        code is not None and code.strip().upper() == rule.code.strip().upper()
    )
    description_ok = rule.description_contains is None or (
        description is not None and rule.description_contains.lower() in description.lower()
    )
    return reason_ok and code_ok and description_ok


def load_taxonomy(path: Path = TAXONOMY_PATH) -> list[TaxonomyRule]:
    """The one I/O boundary in this module. Reads once; callers cache the
    result (see api/normalise/__init__.py) rather than re-reading per call.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = raw.get("rules", []) if isinstance(raw, dict) else raw
    rules: list[TaxonomyRule] = []
    for index, entry in enumerate(entries):
        rules.append(_parse_rule(entry, index))
    return rules


def _parse_rule(entry: dict[str, Any], index: int) -> TaxonomyRule:
    reason = entry.get("reason")
    code = entry.get("code")
    description_contains = entry.get("description_contains")
    if reason is None and code is None and description_contains is None:
        raise ValueError(
            f"taxonomy rule at index {index} sets none of reason/code/description_contains — "
            f"it would match every input. Every rule needs at least one match key."
        )
    fixture = entry.get("fixture") or []
    return TaxonomyRule(
        failure_class=FailureClass(entry["class"]),
        source=entry["source"],
        reason=reason,
        code=code,
        description_contains=description_contains,
        note=entry.get("note"),
        fixture=tuple(fixture),
    )
