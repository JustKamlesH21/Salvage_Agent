# Pitch — the 5-minute script

Plain version. Every beat tells you **what to show**, then **what to say**.

Say the lines in your own words. Keep the **bold** ones close to as written.

---

## Your screen map

Open these before you start. You will only ever switch between five things.

| # | Name it in your head | What it is |
|---|---|---|
| **A** | **Slide** | A title card, or just your face |
| **B** | **Diagram** | `localhost:5173/architecture.html` |
| **C** | **Razorpay** | Razorpay Dashboard, test mode |
| **D** | **Deck** | `localhost:5173` — the main Salvage screen |
| **E** | **Terminal** | One terminal window |

That is it. Five tabs. If you are ever unsure, you are on **Deck**.

---

## Setup, 15 minutes before recording

**1. Start everything**

```bash
make verify                                       # leave the 8/8 on screen in Terminal
DRY_RUN=true SCHEDULER_TICK_SECONDS=5 \
  .venv/bin/python -m uvicorn api.main:app --port 8000 &
cd web && npm run dev &
```

**2. Put some cases on the Deck** (so it is not empty on camera)

```bash
.venv/bin/python scripts/demo.py cohort --delay 2
```

**3. Open a tunnel so Razorpay can reach your laptop**

The webhook you created in Razorpay is still there — you do not make it again.
What does not survive is the *address*: on ngrok's free plan a new one is issued
on every start, so the URL saved in `.env` was already dead.

Fix it once:

1. **dashboard.ngrok.com → Domains → Create Domain.** Free plan includes exactly
   one. Take whatever name it offers — you will never type it again.
2. Put it in `.env`:

   ```
   PUBLIC_WEBHOOK_URL=https://<that-domain>
   ```

3. From then on, every time:

   ```bash
   make tunnel
   ```

   It reads the domain from `.env` and always asks ngrok for the same one, so
   the address never changes again.

*(No reserved domain? `ngrok http 8000` works, but the address changes on every
restart and you re-edit Razorpay each time. Worth the five minutes to avoid.)*

**4. Point Razorpay at it — once**

**Razorpay → Settings → Webhooks.** You already have a Salvage webhook: **open
it and correct the URL.** Do not add a second one — Razorpay delivers to both,
and one failed payment would open two cases in the middle of your demo.

- URL: `https://<your-domain>/webhooks/razorpay`
- Secret: the same value as `RAZORPAY_WEBHOOK_SECRET` in your `.env`
- Events: **`payment.failed`** only

Check it before you trust it, with the tunnel up:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<your-domain>/health
```

`200` means Razorpay can reach you. `404` means the tunnel is down or the URL is
stale — which is exactly the state the old one was in.

**5. Make a Payment Link you will use on camera**

In **Razorpay** → Payment Links → Create Payment Link. Any amount, ₹499 is fine.
Keep the link open in a tab. *(You get 30 links per test account. One is plenty.)*

### Test card details

All three were confirmed against this account on day 1 — see NOTES.md, not from
memory. Re-check razorpay.com/docs/payments/payments/test-card-details/ if
Razorpay changes the list.

**For the video (a Payment Link is a one-time payment):**

| Field | Value |
|---|---|
| Card | `4111 1111 1111 1111` |
| Expiry | any future date, e.g. `12 / 30` |
| CVV | any 3 digits, e.g. `123` |
| Name | anything |

Submit, and Razorpay shows a simulated bank page with **Success** and **Failure**
buttons. **Press Failure.** That is the toggle — there is no real OTP in test mode.

**If you would rather the card decline by itself,** without pressing anything:

| Card | What it does |
|---|---|
| `4100 2800 0008 0001` | Razorpay documents this as `insufficient_fund`. In this account it actually returned `BAD_REQUEST_ERROR` / `payment_failed` — the one generic reason. Either way it fails, which is all beat 3 needs. |

**Not needed for the video, but do not get caught out:** a subscription mandate
needs a different card. `4111 1111 1111 1111` is rejected with *"This card is not
eligible for recurring payments"* — a one-time success card and a
recurring-eligible card are two different things in test mode.

| Card | Use |
|---|---|
| `4718 6091 0820 4366` | Visa domestic. The only card Razorpay documents for subscription/mandate authentication. |

**Two constraints that bite:** a test-mode card token dies after **3 days**, and
you get **30 Payment Links** per test account, ever. Authenticate mandates the
day you record, not before.

**6. Rehearse the failure once.** Open the link, pay with the card above, and
press **Failure** on the bank page. Check a case appears on the **Deck** within
about ten seconds. If it does, you are ready. If it does not, use the fallback in
beat 3 and nothing is lost.

**7. Two minutes before you start recording**, run this in **Terminal**:

```bash
.venv/bin/python scripts/demo.py soon --minutes 6
```

It prints a case id. You will need it in beat 6.

---

# The script

## Beat 1 — 0:00 to 0:40 — the problem

**SHOW: Slide** (your face, or a title card)

**SAY:**

> Last year my gym membership got cancelled, and I did not cancel it.
>
> The auto-debit ran on the 28th. My salary comes on the 1st. So it failed. Not
> because I left, not because my card was dead. It just ran three days early.
>
> Then it tried again the next day. Failed. And the day after. Failed. And the
> day after that.
>
> **On the fourth day the subscription was cancelled, and at ten to eleven at
> night I got a WhatsApp asking me to pay.**
>
> I never signed up again. The gym lost about eighteen thousand rupees a year,
> because their payment system retried on a timer instead of asking why.

*Pause here. Then:*

> That retry is not badly built. It is exactly what Razorpay does, and it is a
> safety net. It just does not know anything about why the charge failed.
>
> Salvage is the part that does.

---

## Beat 2 — 0:40 to 1:15 — how we solve it

**SHOW: Diagram.** Scroll to the timeline picture (section 2).

**SAY:**

> Here is the gap. The three boxes on top are Razorpay's retries. Same schedule
> every time, whether your card is dead or your salary is two days away. After
> the third one, the subscription stops.
>
> The green box underneath is what Salvage adds. One attempt, on payday.

*Point at the green line.*

> And this bit matters: Razorpay's own documentation says a manual charge on an
> invoice does not use up your three retries. So we add a smart attempt on top of
> the safety net, instead of spending it.

**Scroll down to the six boxes (section 3).**

> Six steps between the webhook and the money. **The AI does one of them. It
> reads the failure and tells us why. It never decides to move money.**
>
> That is the whole design. Everything after this is me showing you it is
> enforced, not just promised.

---

## Beat 3 — 1:15 to 2:00 — make a payment fail, live ★

**SHOW: Razorpay,** with your Payment Link open. Then switch to **Deck**.

**Do this, on camera:**

1. Open your Payment Link
2. Card `4111 1111 1111 1111`, expiry `12 / 30`, CVV `123`
3. Submit — Razorpay shows a simulated bank page
4. Press **Failure**
5. Switch to **Deck**

**SAY, while doing it:**

> Let me just break one. This is Razorpay test mode, my real account, a real
> payment link.

*Click Fail.*

> And that fails.

*Switch to Deck. Wait for the new row.*

> That webhook came from Razorpay, over the internet, to my laptop. We check the
> signature before we read a single field. And there is the case — classified,
> checked, decided.
>
> **Nothing there was pre-loaded. No fixture, no seeded database.**

**Then say this. Do not skip it:**

> One honest thing. Razorpay's test mode lists a hundred and ten different
> failure reasons. **Only one of them actually happens in test mode.** Everything
> else comes back as a generic error. That is written up in our LIMITATIONS file.
>
> So for the interesting cases, I replay a signed webhook instead. Same signature
> check, same code, same eleven gates. Let me show you one.

**If the live payment does not work:** say *"test mode is being test mode, here is
the same thing replayed"*, run `.venv/bin/python scripts/demo.py one --amount 1800000`
in **Terminal**, and carry on. You lose nothing.

---

## Beat 4 — 2:00 to 3:00 — the main screen ★

**SHOW: Deck.** Click the ₹18,000 case.

*(If you re-seeded, run `scripts/demo.py one --amount 1800000` and click that one.)*

**Look at the top of the page first — the row of six steps.**

**SAY:**

> This is where the case is right now. Like tracking a delivery. Except the
> delivery time moved for a reason, and the reason is a rule.

**Now scroll down to the two boxes side by side.**

**SAY:**

> Eighteen thousand rupees. Not enough balance. We work out that this is a
> temporary money problem, not a dead card, and that is not the AI — that part is
> plain code.
>
> We decide to retry it on payday. Then eleven rules run.

*Point at the left box.*

> This is what we wanted to do. Retry the charge. And two rules say no.
>
> The first one: you have to warn the customer 24 hours before you take money.
> We have not. So, no.
>
> The second one: eighteen thousand is over the fifteen thousand limit for taking
> money without extra authentication. So, no.

*Point at the arrow in the middle, then the right box.*

> **And this arrow is the bit I would show you if you only had ten seconds.**
>
> The rule did not just say no. It said what to do instead. So we send the
> warning notice first. And then we put *that* through the same eleven rules from
> scratch.
>
> It does not get to assume its own fix is allowed.

**Now click on one of the red-stamped rules.**

> Every no comes with the actual regulation it came from. And that text is read
> live from the code, so it cannot go stale.

---

## Beat 5 — 3:00 to 3:25 — the two promises, in code

**Here is exactly what to do:**

1. **SHOW: Terminal.** Open `api/diagnose/schema.py`. Scroll to the block that
   says `class DiagnosisContext`. It is about 8 lines. That is all you show.
2. Then **SHOW: Terminal** with your `make verify` 8/8 output.

**SAY, over the code:**

> Two promises, and neither one is a document.
>
> This is everything the AI is allowed to see. Look at it — there is no name, no
> phone number, no email, no amount.
>
> **So leaking a customer's details into the AI is not against our rules. It is
> impossible. The code would not compile.**

**Now switch to the `make verify` output.**

> And second: exactly one file in this project is allowed to talk to Razorpay.
>
> Line four there is the check. If a second file ever tries, the build fails. Not
> a code review. The build.
>
> Line five is the same idea for the rules. Every rule has to name the regulation
> it comes from, or it does not ship.

**That is the whole code section. Do not open anything else.** Twenty-five
seconds, then move on.

---

## Beat 6 — 3:25 to 4:05 — it does this on its own ★

### What you are showing, in order

| When | Screen | What is on it |
|---|---|---|
| Start | **Deck** | The cases table. Find your `soon` case. Look at the **Next** column. |
| | | It says something like **`in 1m 20s`** and next to it **`G2_PREDEBIT`**. |
| | | **The number counts down while you talk.** That is your visual. |
| Wait | **Deck** | It reaches zero and the row changes by itself |
| Then | **Deck → case** | Click that case. Scroll to the tapes. **There are now two.** |

**Finding the row:** the table is newest first, so your case is at or near the
top. It is the one with a live countdown in the **Next** column.

**Ignore the rows further down that say `due now`.** Those are cases from
earlier runs, from before the scheduler existed, and they will never fire — the
scheduler refuses them on purpose because their record does not say what amount
was checked. Nobody will ask. If someone does, that refusal is the system being
careful, and it is in NOTES.md.

**SAY, while it counts down:**

> Now look at this row. This case is waiting.
>
> Not stuck. Waiting, and it tells you why. That rule there moved it, because
> the warning notice went out a few minutes ago and the regulation says you have
> to wait twenty-four hours.
>
> And that number is counting down on its own.

*(Small honest note, say it here:)*

> I shortened that wait for this video. I moved the clock, not the rules.

**When it hits zero:**

> There. It just went off by itself. Nobody clicked anything.
>
> And here is the important part. At that exact moment, it went back to the
> database, looked up the whole history again, and ran all eleven rules a second
> time.

**Click into the case. Point at the second tape.**

> Look what it decided. **It said no again.**
>
> I moved the clock forward. I did not make the notice any older. The regulation
> wants twenty-four hours and it has had five minutes. So it refused, and
> rescheduled itself.
>
> **This is the whole point. Checking the rules when you schedule something is
> not enough. You have to check them again when it actually runs.**
>
> That is the thing my gym's system did not do at eleven at night.

---

## Beat 7 — 4:05 to 4:35 — the numbers ★

**SHOW: Deck** → click **How it works** in the top bar → scroll to the bottom
(the four number boxes).

**SAY:**

> Forty subscriptions, three runs. We report a range, not an average with a
> confidence interval, because three runs does not earn one.

*Point at the first box.*

> **And here is the number I did not want.**
>
> We do not recover more money than three blind retries do. If I change my
> simulator's settings by twenty percent, the winner flips. So we are not
> claiming we make more money. That box says "not distinguishable" because that
> is what the test says.

*Point at the other three boxes.*

> What is true every single time:
>
> We get the same result with about a third fewer attempts.
>
> We spend zero attempts on dead cards. The old way burns thirty a run, retrying
> cards that will never work, because it does not know they are dead.
>
> And zero messages outside the legal hours. The old way sends nineteen to
> twenty-nine of them per run.
>
> **We checked that last one by looking at what actually got sent, not by asking
> our own rules whether they worked.**

---

## Beat 8 — 4:35 to 5:00 — the close

**SHOW: Deck** → click **Exception desk** → then back to **Deck** and click the
red **kill switch** button, top right.

**SAY:**

> This is everything the agent refused to decide. If it does not understand a
> failure, it hands it to a human instead of guessing with someone's money.

*Click the kill switch. The bar turns red.*

> And there is a stop button. It is the first rule in every check, it needs no
> password, and nothing moves while it is on.

**SHOW: Slide** (back to your face)

> Every number here comes from a simulator, because test mode would not give me
> realistic failures. Our comparison is built from Razorpay's documentation, not
> measured. And we have not built the part that moves real money yet.
>
> All of that is written down in our limitations file.
>
> **What we can prove is smaller than what we set out to build. I would rather
> tell you that than have you find it.**
>
> My gym did not lose me because their system retried. It lost me because it
> retried without asking why, and then messaged me at eleven at night.
>
> Salvage is the part that asks why. And the part that is not allowed to message
> you at eleven at night.

---

## One-page cheat sheet

| Beat | Screen | The one thing to show |
|---|---|---|
| 1 | Slide | You. The gym story. |
| 2 | Diagram | Timeline picture, then the six boxes |
| 3 | Razorpay → Deck | Click Fail. New case appears. |
| 4 | Deck → case | **Two boxes side by side, and the arrow** |
| 5 | Terminal | `DiagnosisContext` (8 lines), then verify 8/8 |
| 6 | Deck | **The countdown in the Next column**, then two tapes |
| 7 | How it works | Four number boxes. Lead with the bad one. |
| 8 | Exception desk → kill switch | Bar turns red |

**Six lines to keep:**

1. "At ten to eleven at night I got a WhatsApp asking me to pay."
2. "The AI reads the failure. It never decides to move money."
3. "It does not get a chance to assume its own fix is allowed."
4. "It said no again."
5. "Here is the number I did not want."
6. "Salvage is the part that asks why."
