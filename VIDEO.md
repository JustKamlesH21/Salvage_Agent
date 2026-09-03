# Video — shot list and script

**I cannot record this.** No screen capture, camera or audio is available to
me, so what follows is the recording script: every beat with the exact
commands, URLs and numbers to say, written to be read while recording. The
numbers are the real ones from `eval/results.json`.

Runs about 5:00. The three beats that carry the submission are marked
**★**. If you overrun, cut 4:20 before you cut any of them.

---

## Before you press record

```bash
make verify                                   # 8/8 on camera-adjacent terminal

# The API, with the scheduler live. This is new and it is the point of §3.
DRY_RUN=true SCHEDULER_TICK_SECONDS=5 \
  .venv/bin/python -m uvicorn api.main:app --port 8000 &

cd web && npm run dev &                       # localhost:5173
```

Have open: the Command Deck (`localhost:5173`), two terminals, and
`LIMITATIONS.md`.

**Set the stage — do this before recording, not on camera:**

```bash
.venv/bin/python scripts/demo.py cohort --delay 2
```

Nine failures, one per class. Leave them on the deck as your starting state.

**Then, ~3 minutes before you reach §3**, in the second terminal:

```bash
.venv/bin/python scripts/demo.py soon --minutes 4
```

It prints a case id and a due time. That case fires while you are talking.
Have the id ready.

**Say the honest thing about `soon` when you get there.** It fails the same
subscription twice, so G2_PREDEBIT genuinely refuses the second charge — the
pre-debit notice went out minutes ago and the framework wants 24 hours — and
then it moves that scheduled time to four minutes out. The gates are not
touched. What is shortened is the wait, and nothing else. That sentence costs
you four seconds and buys the whole beat's credibility.

---

## 0:00–0:30 · The gap

> "When a recurring charge fails, Razorpay retries it three times — T+1, T+2,
> T+3 — and then halts the subscription. Those retries are blind. They don't
> know whether the card is dead or the customer is three days from payday.
> Salvage sits in that gap."

Show: `localhost:5173/#/how-it-works`, section 01. The timeline is on screen —
Razorpay's three rungs above the line, Salvage's diagnosed attempt below it.

> "And the lever is documented, not clever. A manual charge on an issued
> invoice doesn't count against the subscription's remaining retries. So
> Salvage adds precisely-timed attempts without spending the platform's safety
> net."

## 0:30–1:00 · The thesis, then make it happen live ★

> "The LLM diagnoses. It never decides to move money."

Show section 02 — the six stages, and the two boundaries beside them.

> "That isn't a policy we wrote down and hoped for. `DiagnosisContext` has no
> field for a name, a contact, an email or an amount, so leaking one into a
> prompt is a type error. And the build fails if a second file ever imports the
> Razorpay SDK."

Now switch to the deck and run, on camera:

```bash
.venv/bin/python scripts/demo.py one --amount 1800000
```

> "That's a signed webhook, posted the way Razorpay posts one. Nothing was
> written to the database — signature verified on the raw body, appended to the
> event chain, normalised, diagnosed, policy, eleven gates, executor."

The case appears in the live feed. Let it.

## 1:00–2:15 · The case, and the substitution ★

Open the ₹18,000 case. **This is the screenshot in the README.**

Top of the screen first — the journey rail:

> "Where it is, and what it's waiting for. Like a parcel, except the delivery
> estimate moved for a reason, and the reason is a clause."

Then the Decision Tape:

> "₹18,000, insufficient funds. The normaliser classifies it deterministically
> as temporary liquidity — no model involved. Policy proposes a scheduled
> retry. Then eleven gates run. All of them, in order, every time, even after
> one has already refused — the tape shows the whole chain, not the first
> refusal."

Point at the two cards side by side.

> "Twenty-two gate verdicts on one decision. Eleven for the retry it wanted to
> make — G2 refuses it, no pre-debit notice has been sent and the e-mandate
> framework wants twenty-four hours' notice; G3 refuses it, ₹18,000 is over the
> ₹15,000 no-AFA ceiling. And eleven more for the pre-debit notice it sent
> instead, which is what that arrow is: G2 didn't just say no, it said what to
> do instead, and that substitute went through the same eleven gates from
> scratch."

Click any gate row.

> "Every refusal carries the clause it came from, and the rule text is read
> live off the gate module — not transcribed into a document that can drift."

Click G8 or G1, where the scope note shows:

> "And where the gate checks something narrower than the clause it cites, it
> says so. G8's clause says every collection must be followed by a
> confirmation. Salvage doesn't send those. What it does is refuse a charge to
> a customer who has no channel to be confirmed on. That distinction is on the
> screen because a judge who reads the clause and assumes the behaviour would
> be wrong, and that's our fault to prevent."

## 2:15–3:00 · Fire-time re-evaluation, live ★

Go back to the deck. The case from `soon` is counting down in the **Next**
column.

> "This one is waiting. Not stuck — waiting, with a reason: G2_PREDEBIT moved
> it, because the notice went out minutes ago and the framework wants
> twenty-four hours."

*(Say the `soon` caveat here — see "Before you press record".)*

Watch it hit zero. The scheduler ticks every five seconds.

> "It just fired. And what matters is what happened at that moment: the gate
> context was rebuilt from the database — the audit rows, the contacts, the
> opt-out state — and all eleven gates ran again against the world as it is
> now, not as it looked when the action was scheduled."

Open the case. There is a second tape.

**Expect it to be refused again, and lead with that.** Bringing the clock
forward does not make the pre-debit notice older, so G2 refuses the charge a
second time and re-schedules it. That is the strongest version of this beat:

> "And look what it did — it refused it again. I moved the *wait* forward; I
> didn't move the notice. The framework wants twenty-four hours and it has had
> four minutes, so G2 said no a second time and scheduled it again. That is the
> whole argument: compliance that's only checked when you schedule something
> isn't compliance. A retry scheduled at a compliant moment that comes due at
> 19:32 gets refused at 19:32."

> "Same diagnosis, same policy rule, eleven fresh verdicts. Nothing was carried
> over except what was actually true."

*(If it does dispatch instead — the notice was genuinely old enough — say so:
the gates cleared it on the second pass and the action went out. Both outcomes
make the same point.)*

## 3:00–3:40 · The numbers, including the one we didn't want ★

`#/how-it-works`, section 05.

> "Forty mandates, three seeds, three arms. Reported as a range across seeds,
> not a confidence interval — N=3 doesn't support one and we don't imply it."

> "Here's the number we didn't want. Salvage does **not** recover more money
> than the baseline. And when we run the ±20% sensitivity check, the ranking
> flips. The recovery comparison sits inside the noise of a calibration we
> wrote ourselves, so we're not claiming an uplift. That card says 'not
> distinguishable' because that's what the run says."

> "What holds on every seed, regardless of calibration: Salvage reaches
> comparable recovery on about 35% fewer attempts. Zero attempts spent on
> declines that can never recover — the baseline spends thirty a run retrying
> dead cards three times each, because it doesn't know what it's retrying. And
> zero contacts outside the 08:00–19:00 window, against the baseline's nineteen
> to twenty-nine per run — well over half its cases."

> "We audited that last row from the record of what was dispatched and when,
> without reading a single gate verdict. A gate bug shows up as a number there
> instead of hiding behind the gate that got it wrong."

> "And we measured what the model contributes. Across the batch it drove six of
> six hundred and twenty-three decisions, at about forty times the latency. The
> deterministic normaliser wins almost every time, by design. We're reporting
> that because it's what we measured."

## 3:40–4:20 · What broke

**Duplicate webhook**, live:

```bash
# post the same signed payload twice
{"status":"received"}
{"status":"duplicate"}
```

> "Same event id replayed. One case, one set of actions — a unique index does
> it, not a check-then-act race."

**The one worth naming.** Show `NOTES.md`:

> "The batch found a bug by having the baseline beat us. Our headline lever —
> schedule to the customer's payday instead of retrying now — was specified on
> day three and the pipeline was dropping the timing on the floor for two days.
> Every 'scheduled' retry fired immediately. That's the baseline's behaviour
> wearing our name, and `make verify` was green the whole time, because nothing
> tested that a timing intent became a time. A measurement caught what three
> reviewers and a full gate suite didn't."

Then, if you have the seconds:

> "And building the live scheduler found two more. Case ids are ObjectIds; we
> stringified one, and the tick silently fired nothing — while the tests passed,
> because the fixtures used string ids. The tests agreed with the code because
> they shared its assumption. That's the third time that exact shape has bitten
> this project and it's in NOTES.md each time."

**The chain**:

```bash
.venv/bin/python scripts/verify_chain.py
```

> "Tamper-evident, not tamper-proof. It detects modification; it doesn't
> prevent it. Production needs WORM storage, and that's in LIMITATIONS.md."

## 4:20–4:50 · The edges, and the switch

`#/exceptions`:

> "What the agent refused to decide, and why. An UNKNOWN failure class escalates
> to a human rather than guessing at a money action. Below it, pipeline failures
> — separately, because one is the agent working and the other is the agent
> broken."

Click the kill switch. The bar turns red.

> "And there's a switch. G0_KILL is evaluated first in every chain, it needs no
> token, and nothing dispatches while it's engaged — including the scheduler,
> which freezes rather than cancelling. Release it and the scheduled work is
> still owed."

## 4:50–5:00 · Limitations

> "Every number here comes from a simulator with zero measured parameters,
> because Razorpay test mode wouldn't produce realistic declines — one of a
> hundred and ten published error reasons reproduced at all. The baseline's
> retry ladder is doc-derived, not observed. Live dispatch isn't built: the
> executor's Razorpay call raises `NotImplementedError` by choice, so every
> number here is dry-run. And if the executor crashed mid-dispatch, nothing
> currently resolves the in-flight row."

> "It's all in LIMITATIONS.md, which is harsher than you were going to be."

---

## Numbers, for reference while recording

| | baseline (A) | Salvage (B) | Salvage, no LLM (C) |
|---|---|---|---|
| Net recovery range | 20.9–46.1% | 30.9–40.9% | 30.9–40.9% |
| Net recovery mean | 35.4% | 34.8% | 34.8% |
| Attempts per recovery | 4.09–5.30 | 2.89–3.39 | 2.89–3.39 |
| Wasted on hard declines | 30/run | 0 | 0 |
| Contacts outside window | 19–29/run | 0 | 0 |
| Decision latency p50 | — | 279ms | 6ms |
| LLM-driven decisions | — | 6 of 623 | 0 |

Sensitivity at ±20%: **ranking flips**. `ranking_by_net_recovery_rate` is
`["A", "B=C"]` — the baseline ranks first, and the page says so.

**Per-seed vs totals:** the compliance card totals three runs (70 contacts
outside the window) and labels it. Say "nineteen to twenty-nine per run" and
the two agree.

---

## If something goes wrong on camera

- **Nothing fires at §3.** The scheduler refuses a decision with no
  `amount_paise` — anything created before day 6 has none. Re-run
  `scripts/demo.py soon` and use the fresh case.
- **Every charge refuses on G8_POSTDEBIT.** The customer has no contact channel
  on record. `scripts/demo.py` seeds one; `--no-customer` deliberately does
  not. That refusal is correct behaviour and is worth ten seconds if it happens.
- **The deck is empty.** The API is down or on the wrong port. The screen tells
  you which, and says how to start it.
