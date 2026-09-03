# Pitch — the 5-minute narration

`VIDEO.md` is the shot list. This is the *story*: what you say, in what order, and
why each beat is there. Track 03 is judged on the problem and the solution, not
on feature count, so the arc below spends its first minute on the problem and
never stops referring back to it.

Read the spoken lines as written or in your own words — but keep the **bolded
sentences verbatim**. Those are the ones doing the work.

---

## The decision you have to make first: how live is live?

You asked whether to make a real payment on camera. **Yes — open with one.** It
answers "is this hardcoded?" before a judge thinks to ask it, and nothing else
answers it as cheaply.

But be clear-eyed about what test mode will give you, because the limitation is
itself worth saying out loud:

| | Real Razorpay test payment | Replayed signed webhook |
|---|---|---|
| Signature | Razorpay's, over the wire | Ours, same HMAC check, same code path |
| Failure reason available | `payment_failed` only | Any of the 110 published reasons |
| Classifies as | `CHANNEL_DOWNTIME` | any of the 9 classes |
| Can show the ₹18,000 AFA case | **No** | Yes |
| Subscription-mandate decline | **No** — checkout has no fail option | Yes |

`LIMITATIONS.md` records the reason: **1 of 110 published decline reasons has a
real fixture behind it.** A card documented as `insufficient_fund` returned a
generic `BAD_REQUEST_ERROR` in this account.

So the shape is: **one real payment to prove the pipe is real, then replays to
show the cases test mode cannot produce — and say exactly why you're switching.**
That sentence is worth more than a fully-live demo would be, because it shows you
found the platform's edge instead of pretending it wasn't there.

### Before you record — the live-payment setup

```bash
ngrok http 8000                      # copy the https URL it prints
```

Put that URL + `/webhooks/razorpay` into the Razorpay Dashboard →
Settings → Webhooks, subscribed to `payment.failed`, with the same secret as
`RAZORPAY_WEBHOOK_SECRET`. Free ngrok URLs rotate, so the one in `.env` is stale
— re-do this on the day.

Then create a test payment link or checkout for a small amount and fail it.
**Rehearse this once before recording.** If the webhook does not arrive within
about ten seconds, fall back to `scripts/demo.py one` and use the "replay" line —
it costs you nothing.

### Everything else running

```bash
make verify                                       # 8/8, leave it on screen
DRY_RUN=true SCHEDULER_TICK_SECONDS=5 \
  .venv/bin/python -m uvicorn api.main:app --port 8000 &
cd web && npm run dev &
.venv/bin/python scripts/demo.py cohort --delay 2   # background cases, off camera
```

**Three minutes before you reach beat 6**, in a second terminal:

```bash
.venv/bin/python scripts/demo.py soon --minutes 4
```

**Tabs to have open:** the Command Deck, the architecture page
(`localhost:5173/architecture.html`), `LIMITATIONS.md`, and an editor on
`api/diagnose/schema.py`.

**The case the script names:** `6a99566a217e50181ae63811` — ₹18,000, insufficient
funds, 22 verdicts, G2 and G3 both refusing. If you re-seed, mint a fresh one with
`scripts/demo.py one --amount 1800000` and use the id it prints.

---

## Beat 1 · 0:00–0:40 · The problem, as it happened to you

**On screen:** you, or a plain title card. No UI yet.

> "Last year my gym membership was cancelled and I didn't cancel it.
>
> The auto-debit ran on the 28th. My salary lands on the 1st. So it failed —
> not because I'd left, not because the card was dead, just because it ran three
> days early. Then it retried. Same time the next day, and the day after, and the
> day after that. All three failed for the same reason: the money still wasn't
> there yet.
>
> **On the fourth day the subscription was cancelled, and at ten to eleven that
> night I got a WhatsApp asking me to pay.**
>
> I never resubscribed. The gym lost about eighteen thousand rupees a year, and
> they lost it because their payment system retried on a timer instead of asking
> why."

*Pause. Then the turn:*

> "That retry wasn't badly built. It's exactly what Razorpay does, by design —
> T+1, T+2, T+3, then halt. It's a safety net, and it works. It just doesn't
> know anything about *why* the charge failed. Salvage is the part that does."

**Why this beat works:** it is one story, it is true, and it contains both
failures at once — the merchant lost the revenue *and* the customer got contacted
at 22:50, which is illegal under the RBI Fair Practices Code. The rest of the
demo pays off both halves.

---

## Beat 2 · 0:40–1:15 · The shape of the solution

**On screen:** `localhost:5173/architecture.html`, section 2 — the retry timeline.

> "Here's the gap. Razorpay's three retries, above the line. They're blind: same
> schedule, same hour, whether the card is dead or the customer is three days from
> payday. After the third one, the subscription halts."

Scroll slightly so the green marker is visible.

> "Salvage puts one attempt below the line, at the next payday window. And this
> is the part I want to be precise about, because it's a documented Razorpay
> behaviour and not a trick: **a manual charge on an issued invoice doesn't count
> against the subscription's remaining retries.** So the intelligent attempt goes
> on top of the safety net instead of spending it."

Scroll to the pipeline.

> "Six stages between the webhook and the money. **The model diagnoses. It never
> decides to move money.** That's the whole architecture in one sentence, and
> everything after this is me showing you it's enforced rather than promised."

---

## Beat 3 · 1:15–2:00 · A real payment, failing, live ★

**On screen:** split — browser with the Razorpay test checkout, and the Command
Deck.

> "So let's make one fail. This is Razorpay test mode, my real keys, a real
> checkout."

Fail the payment. Switch to the deck.

> "That webhook just came from Razorpay, over the internet, to my machine. The
> signature is verified against the raw bytes before anything parses it. And
> there's the case — classified, gated, decided, in under a second."

Let the live feed line land.

> "**Nothing about that was staged. There's no fixture behind it and no database
> seeding.** The event went into an append-only hash-chained log, the normaliser
> classified it, policy chose a play, eleven gates ran, and the executor wrote its
> audit row."

**Now the honest pivot — do not skip this:**

> "Now, one thing I have to tell you. Razorpay test mode publishes a hundred and
> ten decline reasons. **Exactly one of them actually reproduces** — everything
> else comes back as a generic failure, and subscription-mandate checkout has no
> fail option at all. That's in LIMITATIONS.md.
>
> So for the cases test mode can't produce, I replay signed webhooks — same
> signature check, same pipeline, same gates, same code. What I can't fake is the
> gate chain. Let me show you that."

---

## Beat 4 · 2:00–3:00 · The signature case ★

**On screen:** open case `6a99566a217e50181ae63811`.

Start at the top — the journey rail.

> "Where this case is, and what it's waiting for. Like tracking a parcel — except
> the estimated time moved for a reason, and the reason is a clause."

Then the tape.

> "Eighteen thousand rupees, insufficient funds. The normaliser classifies that
> deterministically — no model involved. Policy proposes a scheduled retry, timed
> to payday. Then eleven gates run. **All eleven, in order, every time — even
> after one has already refused**, because the record has to show the whole chain
> and not just the first no."

Point at the two cards.

> "Twenty-two gate verdicts on one decision. Eleven for the retry it wanted to
> make — G2 refuses it, because no pre-debit notice has gone out and the e-mandate
> framework wants twenty-four hours' warning. G3 refuses it too, because eighteen
> thousand is over the fifteen-thousand ceiling for a debit without additional
> authentication.
>
> And eleven more for what it did instead. **That arrow is the part I'd point at
> if you only look at one thing.** G2 didn't just say no — it said what to do
> instead. Salvage substituted the pre-debit notice, and then put *that* through
> the same eleven gates from scratch. It doesn't get to assume its own fix is
> legal."

Click a blocked gate.

> "Every refusal carries the clause it came from. That rule text is read live off
> the gate module — it isn't copied into a document that can drift."

Click **G8_POSTDEBIT**, where the scope note shows.

> "And where a gate checks something narrower than the clause it cites, it says
> so. G8's clause says every collection must be followed by a confirmation.
> Salvage doesn't send those. What it actually does is refuse a charge to someone
> who has no channel to be confirmed on. **A judge who read the clause and assumed
> the behaviour would be wrong, and that's our job to prevent, not yours to
> catch.**"

---

## Beat 5 · 3:00–3:20 · The two boundaries, in code

**On screen:** editor, `api/diagnose/schema.py`, the `DiagnosisContext` dataclass.

Keep this to twenty seconds. Show two things and stop.

> "Two guarantees, and neither is a policy document.
>
> This is everything the model is allowed to see. There's no field for a name, a
> contact, an email, or an amount. **Leaking a customer's details into a prompt
> isn't against the rules here — it's a type error.**"

Switch to the terminal showing `make verify` 8/8.

> "And one file in this codebase may import the Razorpay SDK. Check four in that
> run is the boundary scan — if a second file ever imports it, the build fails.
> Same for check five: every gate has to cite a clause, and every policy rule has
> to explain itself, or it doesn't ship."

---

## Beat 6 · 3:20–4:00 · The thing that makes it an agent ★

**On screen:** back to the deck. The `soon` case is counting down in **Next**.

> "This one's waiting. Not stuck — waiting, and it says why: G2 moved it, because
> the notice went out minutes ago and the framework wants twenty-four hours."

*Say the caveat while you wait:*

> "I've shortened that wait for the recording — I moved the scheduled time, not
> the gates."

Watch it fire.

> "It just fired on its own. And here's what happened in that moment: the gate
> context was rebuilt from the database — the audit history, the contacts, the
> opt-out state — and **all eleven gates ran again against the world as it is
> now**, not as it looked when the action was scheduled."

Open the case; there's a new tape.

> "And look — it refused it *again*. I moved the clock forward; I didn't make the
> notice older. So G2 said no a second time and rescheduled it.
>
> **Compliance that's only checked when you schedule something isn't compliance.**
> A retry queued at a legal hour that comes due at half past seven in the evening
> gets refused at half past seven in the evening. That's the whole argument, and
> that's the thing my gym's payment system didn't do at ten to eleven at night."

*(That callback is the strongest line in the script. Land it.)*

---

## Beat 7 · 4:00–4:35 · The numbers, including the bad one ★

**On screen:** `#/how-it-works`, section 05.

> "Forty mandates, three seeds, three arms. Reported as a range across seeds, not
> a confidence interval — three runs doesn't support one, so we don't imply it."

> "**And here's the number I didn't want.** Salvage does not recover more money
> than three blind retries. Run the twenty-percent sensitivity check and the
> ranking flips. So we're not claiming an uplift — that card says 'not
> distinguishable' because that's what the run says."

> "What does hold, on every seed: comparable recovery for about thirty-five
> percent fewer attempts. Zero attempts spent on dead cards — the baseline burns
> thirty a run retrying instruments that can never succeed. And zero contacts
> outside the eight-to-seven window, against nineteen to twenty-nine per run for
> the baseline."

> "**That last row is audited from the record of what was actually dispatched and
> when — without reading a single gate verdict.** If a gate were wrong, it'd show
> up as a number there instead of hiding behind the gate that got it wrong."

---

## Beat 8 · 4:35–5:00 · The edges, and the close

**On screen:** `#/exceptions`, then the kill switch.

> "What the agent refused to decide, and why. An unrecognised failure escalates to
> a human instead of guessing at a money action."

Click the kill switch. The bar turns red.

> "And there's a stop. It's the first gate in every chain, it needs no token, and
> nothing dispatches while it's on — including the scheduler, which freezes rather
> than cancelling. Let go and the work is still owed."

Close on the story:

> "Every number here comes from a simulator, because test mode wouldn't give me a
> realistic decline distribution — that's in LIMITATIONS.md, along with the fact
> that our baseline is derived from Razorpay's docs rather than measured, and that
> live dispatch isn't built.
>
> **What I'd claim is narrower than what I set out to build, and I'd rather tell
> you that than have you find it.**
>
> My gym didn't lose me because their system retried. It lost me because it
> retried without asking why, and messaged me at eleven at night when it ran out
> of ideas. Salvage is the part that asks why — and the part that isn't allowed to
> message you at eleven at night."

---

## Cheat sheet

**Show, in order:** title/face → architecture page (timeline, pipeline) → live
Razorpay payment → deck → case `6a99566a217e50181ae63811` → gate drawer (G8) →
`DiagnosisContext` → `make verify` → deck countdown → fired case → results →
exceptions → kill switch.

**Code, total ≈ 25 seconds.** `DiagnosisContext` (no PII fields) and the verify
summary. That's it — resist opening anything else. Judges are buying the problem
and the solution; code is evidence, not the subject.

**Lines to keep verbatim:** the WhatsApp at 22:50; "the model diagnoses, it never
decides to move money"; "it doesn't get to assume its own fix is legal";
"compliance that's only checked when you schedule something isn't compliance";
"here's the number I didn't want"; the closing callback.

**If something breaks:** the live payment is the only fragile beat. If the webhook
doesn't arrive in ~10s, say *"test mode is being test mode — here's the same event
replayed"*, run `scripts/demo.py one --amount 1800000`, and carry on. You lose
nothing; that line is on-message.
