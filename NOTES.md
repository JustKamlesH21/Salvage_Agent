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

**2. The hash chain needed a way to survive a concurrent append — and then
that guard turned out to be a batch bottleneck.**
Two writers reading the same chain head would both compute the same `prev_hash`
and fork the chain silently, so `prev_hash` became a unique index: the second
writer fails with `DuplicateKeyError` and retries against the new head. Same
"unique index instead of a lock" move the PRD already makes for idempotency.

A global unique index on `prev_hash` alone, though, means one chain per
collection — and §10 runs twenty seeds across three arms. Sixty runs appending
to one chain serialises every audit write in the batch behind a single
contended head, against NFR-02's three-minute budget. The index is now compound
on `(run_id, prev_hash)`: one chain per run, `run_id` null for live.
`verify_chain.py` walks and reports each partition separately, so a break names
the run it happened in.

Two consequences worth writing down:

- `run_id` had to be added to the `events` and `audit` shapes in PRD §8.2, and
  it is part of the hashed body — so a row cannot be moved between partitions
  undetected.
- The genesis sentinel stopped being merely tidy and became load-bearing.
  `GENESIS_HASH` was already an explicit 64 zeros rather than null, which is
  what makes partitioning safe: MongoDB indexes a missing field and an explicit
  null identically, so a null genesis under a compound index would be
  indistinguishable from a row whose `prev_hash` was never written — a
  malformed document would present as the head of a chain.

*How it was found:* review, not by a check. The harness had nothing that could
have caught it — no check reasons about write throughput. Noted as a hole.
*Class:* ordering / performance.

**2b. An exhausted chain append must be filed, not dropped.**
The retry loop originally raised a bare `RuntimeError` with no jitter.
Deterministic backoff makes two colliding writers re-collide on the same
schedule — a lockstep livelock rather than a resolution — so retries are now
full-jitter, and worst-case total is under a second. On exhaustion the
repository writes a `CHAIN_APPEND_EXHAUSTED` row to `exceptions` carrying the
undelivered document, *then* raises `ChainAppendExhaustedError`. A silently
dropped audit row is worse than a failed action: the action leaves a trace
elsewhere, the missing row leaves none.
*Class:* other — durability.

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
