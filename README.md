# Salvage

Compliance-gated payment recovery agent · Razorpay AI Buildathon 2026 · Track 03

**The LLM diagnoses. It never decides to move money.**

Salvage sits between `payment.failed` and `subscription.halted`. It diagnoses why
a recurring charge failed, chooses the right intervention and the right moment,
forces every money action through hard RBI compliance gates, executes it on
Razorpay test-mode APIs, and measures recovery against a baseline.

> **Status: day 1, in progress.** Scaffolding, the verification harness, the
> MongoDB layer, and the Razorpay seed and fixture-capture machinery. No feature
> code yet, and **no webhook fixtures captured** — day 1's gate to advance is
> "fixtures committed" and it is not met. `make verify` fails on two steps, and
> those failures are the accurate report of what has not been built. See PRD §11
> for what lands when.

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
make verify           # the gate — currently fails, honestly
make dev              # api + web, dry-run mode
```

`DRY_RUN` defaults true. Nothing reaches Razorpay until the system is explicitly
armed with a confirm token, and the kill switch freezes everything within one
scheduler tick.

## Out of scope, and staying that way

Checkout abandonment. B2B receivables. Voice. Chargebacks. Multi-gateway routing
— that is Optimizer's job, not ours. Depth on one loss class with honest numbers
beats four shallow loops.

## Toolchain note

CLAUDE.md targets Python 3.12. This machine has 3.11, 3.13 and 3.14; the `.venv`
was built on **3.13.5** because 3.12 is not installed. `pyproject.toml` declares
`requires-python = ">=3.12"` and ruff/mypy target `py312`, so switching back is
`brew install python@3.12 && make clean && make install PYTHON=python3.12`.
