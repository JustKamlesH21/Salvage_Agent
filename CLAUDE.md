# CLAUDE.md — Salvage

Read this before every task. `PRD.md` is the contract; `SALVAGE_SPEC.md` is the reasoning behind it. When code and PRD disagree, the PRD wins — or you change the PRD in the same commit and say why.

## What this is

A compliance-gated payment recovery agent for the Razorpay Buildathon (Track 03). It decides how to recover failed recurring charges, and it moves real money through Razorpay test-mode APIs. Treat every code path that reaches the executor as financial code.

## Non-negotiable invariants

These are checked mechanically by `scripts/verify.sh`. Do not weaken a check to make it pass — fix the code.

1. **Only `api/executor/client.py` may import the Razorpay SDK.** No exceptions. Everything else goes through `execute_action()`.
2. **The LLM never chooses a money action.** It returns a diagnosis only. If the deterministic normaliser produced anything but `UNKNOWN`, the normaliser wins.
3. **No PII in LLM prompts.** No name, contact, email, or amount. There is a test for this.
4. **Gates cannot be bypassed.** No flag, config value, or argument skips the chain. `G0_KILL` is evaluated first, always.
5. **Gates re-evaluate at fire time**, not only at schedule time.
6. **`events` and `audit` are append-only.** Their repositories expose `insert` only. No `update_one` or `delete_one` against those collections anywhere.
7. **Every gate module exports `CLAUSE_REF` and `RULE_TEXT`.** Every policy rule has `because` and `cites`.
8. **`DRY_RUN` defaults true.** Going live requires an explicit arm call with a confirm token.
9. **Every executor dispatch carries an idempotency key** and inserts its pre-audit row first. `DuplicateKeyError` means already executed — return the prior result, never re-dispatch.
10. **Timestamps stored UTC, gate logic evaluated IST, zone always rendered.**

## Stack

Python 3.12 · FastAPI · Motor (async MongoDB) · Pydantic v2 · APScheduler · Groq (Llama) · pytest
React + Vite + TypeScript · Tailwind · Recharts · SSE
MongoDB Atlas M0. No Redis — unique indexes handle idempotency.

## Commands

```bash
make dev            # api + web, dry-run mode
make test           # pytest
make verify         # THE gate: lint, types, tests, boundaries, clauses, chain
make seed           # create test-mode plans/subscriptions in Razorpay
make batch          # run the A/B/C eval
```

`make verify` must pass before you call any phase complete. If it fails, fix it before moving on. Never report a phase done with a failing verify.

## Verification, and how to use it

`scripts/verify.sh` runs, in order:

1. `ruff check` and `ruff format --check`
2. `mypy --strict api/`
3. `pytest` — with 100% branch coverage required on `api/gates/` and `api/normalise/`
4. `scripts/check_boundaries.py` — Razorpay SDK import confined to the executor; no writes to append-only collections
5. `scripts/check_clauses.py` — every gate has `CLAUSE_REF` + `RULE_TEXT`; every policy rule has `because` + `cites`
6. `scripts/verify_chain.py` — event and audit hash chains intact
7. `cd web && tsc --noEmit && npm run lint && npm run build`

Write checks 4–6 on Day 2, before you need them. They are what make self-correction possible: a failing boundary check tells you exactly what you broke.

## Workflow expectations

- **Plan before you build.** For any task touching gates, policy, or the executor, produce a plan first and get it approved.
- **Test first for gates and the normaliser.** These are the two modules where correctness is the product.
- **One phase per session where possible.** Phases are defined in PRD §11. End each with `make verify` green and a commit.
- **Use the subagents.** `compliance-auditor` reviews every diff touching `api/gates/`, `api/policy/`, or `api/executor/`. `spec-guardian` checks for PRD drift at phase end. Do not skip these because the change looks small — the small changes are where bypasses appear.
- **When you find a real bug, keep the evidence.** Add the failing test, then fix. Bugs found and fixed are demo material for this submission, not embarrassments.

## Style

Tight and practical. No speculative abstraction, no framework where a function will do. If a layer does not earn its existence, delete it. Comments explain *why*, never *what*.

Error messages state what happened and what to do. They do not apologise and they are never vague.

## Things that will bite you

- Verify the webhook HMAC on the **raw body**. Do not parse or cast before verifying.
- Razorpay test-mode card tokens expire in 3 days. Only the ~8 demo cases use the live API.
- 30 Payment Links max per test business. Never create links inside a batch run.
- Manual charge of domestic cards is unsupported — that path goes via Payment Link.
- Razorpay's own auto-retries fire T+1, T+2, T+3. Salvage's manual invoice charges do **not** consume that budget — this is the product's key lever, do not accidentally design it away.
- Server runs UTC. Every gate reasons in IST. Most timezone bugs in this codebase will be here.

## Definition of done, per phase

- `make verify` green
- New behaviour covered by tests, including the failure path
- `compliance-auditor` run on any money-path diff, with findings addressed
- PRD updated if the design changed
- Committed with a message saying what changed and why
