# Salvage

Compliance-gated payment recovery agent · Razorpay AI Buildathon 2026 · Track 03

**The LLM diagnoses. It never decides to move money.**

Salvage sits between `payment.failed` and `subscription.halted`. It diagnoses why
a recurring charge failed, chooses the right intervention and the right moment,
forces every money action through hard RBI compliance gates, executes it on
Razorpay test-mode APIs, and measures recovery against a baseline.

> **Status: complete for submission (compressed 3-day replan, PRD §11).**
> Webhook ingest (HMAC, idempotency, hash-chained storage), the failure
> normaliser, LLM diagnosis, the deterministic policy engine, all eleven
> compliance gates, the executor, the decision pipeline, the calibrated
> simulator, the A/B/C batch, and both frontend screens are built and tested.
> `make verify` is 8/8 green.
>
> Read the **Results** section below before the rest of the README: the batch
> does **not** show Salvage recovering more money than the baseline, and says
> so. What it does show is fewer attempts, zero wasted attempts on hard
> declines, and zero compliance violations.
>
> Deliberate scope cuts, all documented rather than silent: no live Razorpay
> dispatch (`DRY_RUN` stays true, `client.dispatch()` is a stub), no in-doubt
> reconciliation (LIMITATIONS.md, next to FR-28), no S3 Batch Evidence screen
> (the table above replaces it), and no reconciliation sweep (FR-28).

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
| `PITCH.md` | The five-minute narration: the story, and what to show when. |
| `VIDEO.md` | The shot list for the same five minutes: commands, URLs, numbers. |

## Running it

```bash
make install          # .venv + node_modules
cp .env.example .env  # fill in Razorpay test keys and your Atlas URI
make verify           # the gate — 8/8 green
make dev              # api + web, dry-run mode
make batch            # the A/B/C batch → eval/results.json
```

`make dev` serves the Command Deck at `localhost:5173` and the API at
`localhost:8000`. Post a `payment.failed` webhook to `/webhooks/razorpay` and
a case opens, a decision is recorded, and the Decision Tape renders it.

**Three independent barriers stand between this system and real money**, and
all three would have to be removed deliberately:

1. `DRY_RUN` defaults true and is read from the environment at startup. No
   HTTP route changes it.
2. `execute_action()` reaches a live dispatch only when `dry_run` is false
   **and** the process has been armed with the token in `ARM_CONFIRM_TOKEN`
   (invariant 8). `POST /control/arm` calls the real arm function and
   enforces the real token.
3. `api/executor/client.py`'s `dispatch()` is an unimplemented stub. Live
   Razorpay execution (FR-20) is out of scope for this submission, so the
   live branch raises rather than charging anyone.

The kill switch is the opposite: it needs no token, engages instantly from
`POST /control/kill` or the status bar, and `G0_KILL` is evaluated first in
every chain. Stopping never needs protecting.

## Results

40 synthetic mandates × 3 seeds × 3 arms, outcomes from a calibrated simulator
(`sim/`), decisions from the real pipeline. Reported as a **range across
seeds** — never a confidence interval; N=3 does not support one. Produced by
`make batch`, committed verbatim as `eval/results.json`.

- **A — Baseline.** Razorpay's documented default: blind retries at T+1, T+2,
  T+3, plus one generic dunning email. No diagnosis, no gates.
- **B — Salvage.** The full stack.
- **C — Salvage, LLM disabled.** The ablation.

| | A · baseline | B · Salvage | C · Salvage, no LLM |
|---|---|---|---|
| Net recovery (range) | 20.9% – 46.1% | 30.9% – 40.9% | 30.9% – 40.9% |
| Net recovery (mean) | 35.4% | 34.8% | 34.8% |
| Attempts per recovery | 4.09 – 5.30 | 2.89 – 3.39 | 2.89 – 3.39 |
| Wasted attempts on hard declines | **30 per run** | **0** | **0** |
| Contacts outside 08:00–19:00 IST | **22** | **0** | **0** |
| Compliance violations | — | **0** | **0** |
| Decision latency p50 / p95 | — | 279ms / 1100ms | 6ms / 20ms |

### What this does and does not show

**It does not show that Salvage recovers more money.** The means are 35.4% and
34.8%, and the ±20% sensitivity sweep required by FR-12 **reverses the
ranking** — at ×0.8 the baseline leads, at ×1.2 Salvage does. The recovery
comparison sits inside the noise of a calibration we wrote ourselves, and no
number here should be quoted as a recovery uplift. `sim/CALIBRATION.md` says
the same thing in more detail.

**What it does show**, and what holds across every seed regardless of
calibration:

- Salvage reaches comparable recovery on **~35% fewer charge attempts**.
- Salvage spends **zero** attempts on declines that cannot recover. The
  baseline spends 30 per run — it retries dead credentials three times each,
  because it does not know what it is retrying.
- Salvage produced **zero compliance violations**, audited from the record of
  what was dispatched rather than from the gate verdicts that were supposed to
  guarantee it. The baseline's blind dunning email lands outside the RBI
  contact window on roughly a fifth of cases.
- Salvage's spread across seeds is much tighter (30.9–40.9 against
  20.9–46.1). Fewer, better-timed attempts produce a more predictable result.

**The LLM changed nothing measurable.** Across arm B it drove **2 of ~207
decisions**, and arm B matched arm C on every metric in every seed — at
roughly 40× the decision latency. Invariant 2 is exactly why: the
deterministic normaliser wins whenever it is not `UNKNOWN`, which is almost
always. SPEC §5.10 said this finding ships whatever it turned out to be, so
here it is.

**Arm A's ladder is doc-derived, not observed** — day 1 could not reproduce
Razorpay's retry timing in test mode (LIMITATIONS.md). A doc-derived baseline
is weaker evidence than a measured one, and that applies to every row above.

## Out of scope, and staying that way

Checkout abandonment. B2B receivables. Voice. Chargebacks. Multi-gateway routing
— that is Optimizer's job, not ours. Depth on one loss class with honest numbers
beats four shallow loops.

## Toolchain note

CLAUDE.md targets Python 3.12. This machine has 3.11, 3.13 and 3.14; the `.venv`
was built on **3.13.5** because 3.12 is not installed. `pyproject.toml` declares
`requires-python = ">=3.12"` and ruff/mypy target `py312`, so switching back is
`brew install python@3.12 && make clean && make install PYTHON=python3.12`.
