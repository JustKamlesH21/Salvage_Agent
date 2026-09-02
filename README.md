# Salvage

Compliance-gated payment recovery agent · Razorpay AI Buildathon 2026 · Track 03

**The LLM diagnoses. It never decides to move money.**

Salvage sits between `payment.failed` and `subscription.halted`. It diagnoses why
a recurring charge failed, chooses the right intervention and the right moment,
forces every money action through hard RBI compliance gates, executes it on
Razorpay test-mode APIs, and measures recovery against a baseline.

> **Status: day 4 complete (compressed 3-day replan, PRD §11 — see NOTES.md's
> 2026-09-02 entries).** Scaffolding, the MongoDB layer, 54 real Razorpay
> test-mode webhook fixtures, webhook ingest (HMAC, idempotency, hash-chained
> storage, `api/ingest/`), the failure normaliser + LLM diagnosis +
> deterministic policy engine (`api/normalise/`, `api/diagnose/`,
> `api/policy/`), and the eleven compliance gates + executor + pipeline wiring
> (`api/gates/`, `api/executor/`, `api/scheduler/`, `api/pipeline.py`) are
> built and tested. `taxonomy/failure_classes.yaml` maps ~114 of Razorpay's
> published error reasons to SPEC §4.1's 9 classes, sourced live from
> Razorpay's docs; only one (`payment_failed`) has a real fixture behind it —
> the rest are doc-only, and Razorpay's documented decline reasons still
> didn't reproduce in this test account (see NOTES.md). `make verify` is
> 8/8 green for the first time. In-doubt reconciliation is a deliberate scope
> cut (see LIMITATIONS.md, FR-28); no live Razorpay dispatch has been
> exercised — `DRY_RUN` stays true and no demo mandate is authenticated yet
> (CLAUDE.md: not before day 9/10). Frontend (`web/`) lands next.

## Documents

| File | What it is |
|---|---|
| `PRD.md` | The contract. Requirements and the MongoDB data model. Authority. |
| `SALVAGE_SPEC.md` | The reasoning behind the contract. Architecture and research grounding. |
| `CLAUDE.md` | Repo invariants. Ten non-negotiables, all mechanically checked. |
| `ARCHITECTURE.md` | The diagram and why each boundary exists. |
| `COMPLIANCE.md` | Gate ↔ clause mapping with sources. |
| `LIMITATIONS.md` | What this does not do, written harsher than a judge would ask. |
| `NOTES.md` | Running log of surprises and bugs found. |

## Running it

```bash
make install          # .venv + node_modules
cp .env.example .env  # fill in Razorpay test keys and your Atlas URI
make verify           # the gate — 8/8 green
make dev              # api + web, dry-run mode
```

`DRY_RUN` defaults true. `execute_action()` refuses to reach a real dispatch
unless `api.executor.arm.arm(token)` has been called with the token
configured in `ARM_CONFIRM_TOKEN` — `DRY_RUN=false` alone is not enough
(found unenforced by `compliance-auditor`'s Day 4 review and fixed the same
session). The `POST /control/arm` HTTP route that will call `arm()` for a
live operator is not built yet (PRD §7, not in this phase's scope) — today
`arm()` is a Python-level call, exercised directly in tests. The kill switch
freezes everything the moment `KILL_SWITCH=true` is read, no arming involved.

## Out of scope, and staying that way

Checkout abandonment. B2B receivables. Voice. Chargebacks. Multi-gateway routing
— that is Optimizer's job, not ours. Depth on one loss class with honest numbers
beats four shallow loops.

## Toolchain note

CLAUDE.md targets Python 3.12. This machine has 3.11, 3.13 and 3.14; the `.venv`
was built on **3.13.5** because 3.12 is not installed. `pyproject.toml` declares
`requires-python = ">=3.12"` and ruff/mypy target `py312`, so switching back is
`brew install python@3.12 && make clean && make install PYTHON=python3.12`.
