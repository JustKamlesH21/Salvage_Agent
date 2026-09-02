# Video — shot list and script

**I cannot record this.** No screen capture, camera or audio is available to
me, so what follows is the recording script: every beat from SPEC §11's sheet
with the exact commands, URLs and numbers to say, written so it can be read
while recording. The numbers are the real ones from `eval/results.json`.

## Before you press record

```bash
make verify                      # confirm 8/8 on camera-adjacent terminal
DISABLE_LLM=true .venv/bin/python -m uvicorn api.main:app --port 8000 &
cd web && npm run dev &          # localhost:5173
```

Have open: the Command Deck (`localhost:5173`), a terminal, and
`LIMITATIONS.md`. The demo case `sub_demo_8812` (₹18,000, insufficient funds)
is already in the live partition and shows three gates blocking — that is the
Decision Tape shot.

---

## 0:00–0:25 · The gap

> "When a recurring charge fails, Razorpay retries it three times — T+1, T+2,
> T+3 — and then halts the subscription. Those retries are blind. They don't
> know whether the card is dead or the customer is just three days from
> payday. Salvage sits in that gap."

Show: Razorpay's retry documentation line.

## 0:25–0:50 · The thesis

> "The LLM diagnoses. It never decides to move money."

Show: `ARCHITECTURE.md`'s diagram. Point at the two boundaries — the LLM
returns a diagnosis only, and one file imports the Razorpay SDK.

> "That isn't a policy we wrote down. `DiagnosisContext` has no field for a
> name, a contact, an email or an amount, so leaking one into a prompt is a
> type error, not an oversight. And `check_boundaries.py` fails the build if a
> second file ever imports the SDK."

## 0:50–2:00 · Live case walkthrough — **the signature shot**

Open the case. Let the Decision Tape draw.

> "₹18,000, insufficient funds. The normaliser classifies it deterministically
> as temporary liquidity. Policy proposes a scheduled retry. Then eleven gates
> run — all of them, in order, every time, even after one has already
> blocked, because the tape has to show the whole chain and not just the first
> refusal."

Point at the struck-through chits with the red stamps.

> "G2 blocks it: no pre-debit notice has been sent, and the e-mandate
> framework requires 24 hours' notice. G3 blocks it: ₹18,000 is over the
> ₹15,000 no-AFA ceiling. G8 blocks it: there's no channel to send the
> post-debit confirmation to. Each one is stamped with the clause it comes
> from."

> "So Salvage substitutes a pre-debit notice instead — and then re-gates
> *that*, from scratch. G1 replans it to 08:00 tomorrow, because it's 19:32
> and the Fair Practices Code says no customer contact after seven."

> "Twenty-two gate verdicts on this one decision. Eleven for the retry it
> wanted to make, eleven for the notice it made instead."

## 2:00–2:40 · The lever

> "Razorpay's own auto-retries fire T+1, T+2, T+3. A manual invoice charge
> doesn't consume that budget — so Salvage can add precisely-timed attempts
> without spending the platform's safety net."

> "And 'precisely-timed' is doing real work: the policy rule for a liquidity
> failure schedules to the next payday window, not to right now."

Optional and worth 10 seconds if the pacing allows:

> "That lever was specified on day 3 and not actually implemented until day 5
> — the pipeline was dropping the timing intent on the floor. The batch found
> it by having the baseline beat us."

## 2:40–3:20 · Batch results — **say the uncomfortable number**

Show the README's Results table.

> "40 mandates, 3 seeds, 3 arms. Reported as a range across seeds, not a
> confidence interval — N=3 doesn't support one and we don't imply it does."

> "Here's the number we didn't want. Salvage does **not** recover more money
> than the baseline. Mean net recovery, 35.4% against 34.8% — and when we run
> the ±20% sensitivity check the requirements ask for, the ranking *flips*.
> The recovery comparison is inside the noise of a calibration we wrote
> ourselves, so we're not claiming an uplift."

> "What does hold, on every seed, regardless of the calibration: Salvage
> reaches the same recovery on about 35% fewer attempts. It spends **zero**
> attempts on declines that can never recover — the baseline spends thirty a
> run, retrying dead cards three times each because it doesn't know what it's
> retrying. And zero compliance violations, against the baseline's
> twenty-two contacts sent outside the RBI window."

> "We audited that last row from the record of what was dispatched and when,
> without reading a single gate verdict. A gate bug shows up as a number here
> instead of hiding behind the gate that got it wrong."

## 3:20–3:40 · The tradeoff, said plainly

> "We had three days, not ten. We chose to ship the compliance gates and the
> executor complete, and we cut two things on purpose. A statistically
> powered eval — that's why you're getting a range and not a confidence
> interval. And reconciliation for the case where the executor crashes
> mid-dispatch: if that happens, the case sits in an in-flight state and
> nothing currently resolves it. That failure mode is undefined right now.
> It's written down in LIMITATIONS.md rather than hidden."

## 3:40–4:15 · What broke

**Duplicate webhook**, live:

```bash
# post the same signed payload twice
{"status":"received"}
{"status":"duplicate"}
```

> "Same event id replayed. One case, one set of actions — the unique index
> does it, not a check-then-act race."

**Scheduler drift** — show `tests/scheduler/test_runner.py`:

> "A retry scheduled for 10:00, fired at 19:30. The gates re-evaluate against
> 19:30's reality and replan it. Compliance that's only checked when you
> schedule something isn't compliance."

**The chain**:

```bash
.venv/bin/python scripts/verify_chain.py
```

> "Tamper-evident, not tamper-proof. It detects modification; it doesn't
> prevent it. Production needs WORM storage, and that's in LIMITATIONS.md
> too."

## 4:15–4:40 · The exception desk

Show `/exceptions`.

> "What the agent refused to handle, and why. An `UNKNOWN` failure class
> escalates to a human rather than guessing at a money action."

Also worth showing here — the LLM ablation:

> "We measured what the model contributes. Across the whole batch it drove
> **2 of about 207 decisions**, at 40× the latency. The deterministic
> normaliser wins almost every time by design. We're reporting that because
> it's what we measured."

## 4:40–5:00 · Limitations and the kill switch

> "Every number here comes from a simulator with zero measured parameters,
> because Razorpay test mode wouldn't produce realistic declines — one of 110
> published error reasons reproduced at all. The baseline's retry ladder is
> doc-derived, not observed. Cases needing an AFA link resolve as closed
> because nothing tells us the customer finished it."

Click the kill switch. The bar turns red.

> "And there's a switch. G0_KILL is evaluated first in every chain, it needs
> no token, and nothing dispatches while it's engaged."

---

## Numbers, for reference while recording

| | baseline (A) | Salvage (B) | Salvage, no LLM (C) |
|---|---|---|---|
| Net recovery range | 20.9–46.1% | 30.9–40.9% | 30.9–40.9% |
| Net recovery mean | 35.4% | 34.8% | 34.8% |
| Attempts per recovery | 4.09–5.30 | 2.89–3.39 | 2.89–3.39 |
| Wasted on hard declines | 30/run | 0 | 0 |
| Contacts outside window | 22 | 0 | 0 |
| Decision latency p50 | — | 279ms | 6ms |
| LLM-driven decisions | — | 2 of ~207 | 0 |

Sensitivity at ±20%: **ranking flips** (baseline leads at ×0.8, Salvage at ×1.2).
