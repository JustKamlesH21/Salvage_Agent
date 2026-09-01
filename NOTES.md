# NOTES

Running log of surprises, platform behaviour the docs did not mention, and bugs
found. Raw material for the "what broke" beat of the video and for
`LIMITATIONS.md`. Entries are append-only in spirit: correct one by adding to
it, not by deleting it.

Format per the `bug-evidence` skill: what happened, why, how it was found, fix,
class. Mark anything worth naming in the video with `**demo**`.

---

### 2026-09-01 — day 0, scaffolding

Three things surfaced while building the harness, before any feature code
existed. All three are design conflicts in the spec rather than bugs, and all
three are now resolved in `PRD.md` §8.3.

**1. `audit.idempotency_key` cannot be unique on its own.**
SPEC §5.6 writes a `pre` row and a `post` row carrying the same key, but PRD
§8.3 declared `{idempotency_key: 1} UNIQUE`. Under that index the post row is
unwritable. Changed to a compound unique index on `(idempotency_key, phase)`.
The dedupe property invariant 9 depends on is unchanged: a second `pre` insert
for the same key still raises `DuplicateKeyError`, which still means "already
executed".
*How it was found:* writing the repository tests before the executor existed.
*Class:* spec-drift.

**2. The hash chain needed a way to survive a concurrent append.**
Two writers reading the same chain head would both compute the same `prev_hash`
and fork the chain silently. Added a unique index on `events.prev_hash` and
`audit.prev_hash`, so the second writer fails with `DuplicateKeyError` and
retries against the new head. This is the same "unique index instead of a lock"
move the PRD already makes for idempotency, so it costs no new machinery.
*Class:* ordering.

**3. 100% branch coverage of an empty package is a vacuous pass.**
`--cov=api/gates --cov=api/normalise --cov-fail-under=100` reports 100% of zero
statements and exits green while neither module exists.
`tests/test_coverage_floor.py` makes that a visible failure until there is code
to measure. It deletes itself, in effect, on days 3 and 5.
*How it was found:* reading the first green coverage report and not believing it.
*Class:* other — harness honesty.

**Open, for the executor phase (day 6):** where does a BLOCK audit row sit in
the `(idempotency_key, phase)` space? A blocked action that is later allowed at
re-evaluation must not collide with its own earlier block row. Decide before
writing `execute_action()`, not after.

**Toolchain:** Python 3.12 is not installed on this machine. The venv is 3.13.5.
`pyproject.toml` still declares `>=3.12` and targets `py312` for ruff and mypy.
