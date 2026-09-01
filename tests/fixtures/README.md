# Webhook fixtures

Real Razorpay test-mode deliveries, redacted and re-signed. Phase 2's HMAC
verification, idempotency and out-of-order tests all run against these.

**Status: the machinery is built; no deliveries captured yet.** Everything below
step 3 needs a browser and the Razorpay Dashboard, which is why this is a
runbook rather than a script. Work through it in order.

---

## What is in each fixture

| File | Contents |
|---|---|
| `<nnn>-<event>.raw.json` | the body, exactly as it will be verified |
| `<nnn>-<event>.headers.json` | every delivery header, including the signature and `x-razorpay-event-id` |
| `tampered.raw.json` / `.headers.json` | a valid body with a deliberately wrong signature — the negative case |

`x-razorpay-event-id` is the `rz_event_id` that FR-02's replay test dedupes on.
Do not remove it.

## About the signature — read this before writing an HMAC test

The committed bodies are **redacted**, and redaction changes the bytes, so
Razorpay's original signature no longer verifies over them. A fixture cannot be
both redacted and carry a genuine Razorpay signature; those requirements are
incompatible.

So each fixture is **re-signed** over its redacted bytes with a throwaway
constant, `salvage_fixture_webhook_secret`, defined in
`scripts/redact_fixtures.py`. The signature in the committed headers is valid
over the committed body. Verifying with that secret exercises our HMAC code
path exactly as a real delivery would.

What this does **not** prove is that our HMAC agrees with Razorpay's — the same
mistake on both sides of an equals sign passes every self-signed test. That gap
is closed once, by hand, by `tests/local_only/test_real_signature.py`, which
runs against the uncommitted originals with the real secret. It is gitignored
and excluded from the default pytest run. Run it on day 1 and record the result
in `NOTES.md`.

## Reproducing these

Originals land in `tests/fixtures/_unredacted/` (gitignored, never committed).
`scripts/redact_fixtures.py` turns them into what is committed here. It is
deterministic: re-running produces byte-identical files, so it will not churn
git. **Never hand-edit a fixture** — change the redactor and re-run.

---

# Runbook

## 1. Seed the account

```bash
.venv/bin/python scripts/seed_razorpay.py            # report only
.venv/bin/python scripts/seed_razorpay.py --apply    # create
```

Idempotent. Ids land in `.razorpay-manifest.json` (gitignored). Re-running
prints `exists` for everything and creates nothing.

**It does not authenticate any card mandate, on purpose.** Test-mode card
tokens live 3 days. See step 10.

## 2. Start the capture receiver

```bash
.venv/bin/python scripts/capture_webhooks.py --port 8001
```

Leave it running. It prints a line per delivery. `GET /health` lists what has
landed so far.

## 3. Open a public tunnel

Razorpay only delivers to ports 80/443, so localhost will not work.

```bash
brew install ngrok          # not currently installed on this machine
ngrok config add-authtoken <token from dashboard.ngrok.com>
ngrok http 8001
```

Copy the `https://….ngrok-free.app` URL. Put it in `.env` as
`PUBLIC_WEBHOOK_URL` so the next person does not have to guess.

> The URL changes every time ngrok restarts unless you have a reserved domain.
> If you restart it, redo step 4.

## 4. Register the webhook — Dashboard

There is no API for this on a standard test account.

1. Go to **dashboard.razorpay.com** → make sure the **Test Mode** toggle is on.
2. **Settings → Webhooks → Add New Webhook**.
3. **Webhook URL**: your ngrok URL + `/webhooks/razorpay`
4. **Secret**: paste the same value as `RAZORPAY_WEBHOOK_SECRET` in `.env`.
   If they differ, every signature check fails and step 11 will tell you so.
5. **Active Events** — tick exactly these six:
   `payment.failed`, `subscription.pending`, `subscription.halted`,
   `subscription.charged`, `invoice.paid`, `payment_link.paid`
6. **Create Webhook**.

**Check:** the Dashboard fires a test ping on creation. Your terminal should
print a `captured 001-…` line within a few seconds. If nothing appears, the
tunnel is not reaching the receiver — curl your ngrok URL and confirm you get a
404 rather than a connection error.

## 5. Authenticate one subscription — browser

Needed before any charge event can fire.

1. Open `.razorpay-manifest.json`, find `subscriptions.sub_business.short_url`.
2. Open it in a browser.
3. Pay with `4718 6091 0820 4366` (Visa, domestic) — Razorpay's own
   **Test Cards for Subscriptions** table
   (razorpay.com/docs/payments/payments/test-card-details/), the only domestic
   card explicitly marked for subscription/mandate authentication.

   **`4111 1111 1111 1111` will NOT work here** — found the hard way on day 1.
   It is a one-time-success card; authenticating a subscription mandate needs a
   card explicitly enabled for recurring/tokenized payments, a different
   Razorpay capability from "this card succeeds a payment." The sheet rejects
   it with *"This card is not eligible for recurring payments."* If Razorpay
   changes this list, re-check the docs page rather than trusting this number
   forever — but as of day 1 it is confirmed working. See NOTES.md.

4. Any future expiry, any CVV, OTP `1234` (Razorpay's test OTP page accepts
   anything).

**Expect:** `subscription.charged` and `invoice.paid` in the terminal.
**That is fixtures 2 and 3.**

> This mints a card token that dies in 3 days. Fine for fixtures — this
> subscription is not used in the demo.

## 6. `payment.failed`, reason 1 — insufficient funds

1. Open `subscriptions.sub_enterprise.short_url` from the manifest.
2. Pay with `4718 6091 0820 4366` again (step 5's card is the only confirmed
   recurring-eligible domestic card as of day 1), but choose the **Failure**
   button on the mock bank page instead of Success, or use the OTP value
   Razorpay documents for a forced decline. A pure decline-only card
   (`4000 0000 0000 0002`-style) is not confirmed recurring-eligible and may
   hit the same rejection step 5 did — don't assume it, check.

**Expect:** `payment.failed`, then `subscription.pending`.
**Fixtures 4 and 5.**

## 7. `payment.failed`, reasons 2 and 3 — other decline codes

Repeat step 6 against `sub_mf.short_url` with different failure cards from
**Razorpay Docs → Payments → Test Card Details**. Aim for at least three
distinct `error_reason` values across all captures — the day 3 normaliser needs
variety, and every distinct reason you capture is one fewer guess in
`taxonomy/failure_classes.yaml`.

**If a card produces a reason already captured**, that is fine; note it and move
on. Test mode does not expose the full production decline range.

## 8. `payment_link.paid` — costs 1 of 30 links

**Budget check: 30 payment links per test business, ever. 2 are budgeted here.**

```bash
.venv/bin/python - <<'EOF'
from api.executor.client import get_razorpay_client
link = get_razorpay_client().payment_link.create({
    "amount": 1500100, "currency": "INR",
    "description": "Salvage re-auth link — fixture capture",
    "reference_id": "fixture-reauth-001",
    "notify": {"sms": False, "email": False},
    "reminder_enable": False,
})
print(link["id"], link["short_url"])
EOF
```

Open the `short_url`, pay with `4111 1111 1111 1111`.

**Expect:** `payment_link.paid`. **Fixture 8.**

Record the link id in `NOTES.md` against the running total.

## 9. `subscription.halted` — the slow one

Cannot be forced. It arrives only after Razorpay's own T+1, T+2, T+3 retries
all fail, so it is **three real days** away.

`sub_halt_clock` exists for exactly this, but **its clock has not started.**
A subscription in `created` state has no mandate, so it never charges and never
fails. The three days begin only once step 5 authenticates it and forces the
first failure — which is why step 5 does that subscription first.

Once the clock is running, leave the webhook registered. When the delivery
lands (three days later), run step 11 again and the fixture appears.

`tests/ingest/test_out_of_order.py` is already written and marked `xfail`
against this fixture, so it turns green on its own when the payload arrives.
**If it has not landed by day 5, say so — do not hand-write the payload.**

## 10. Do NOT authenticate the demo mandates yet

Test-mode card tokens are valid **3 days**. Submission is ~11 Sep.
Anything authenticated today is dead on the 4th.

**The ~8 demo-case mandates get authenticated on day 9 or 10, never earlier.**
This is a rule in `CLAUDE.md`. The subscriptions above are fixture-capture only
and are expected to expire.

## 11. Redact and commit

```bash
.venv/bin/python scripts/redact_fixtures.py
.venv/bin/pytest tests/local_only/ -v      # the real-secret check, run once
make verify
```

Then update this file's table below with what actually landed, and put the
payment-link count and token expiry dates in `NOTES.md`.

---

## Captured so far

| # | File | Event | Produced by | Subscription state at capture |
|---|---|---|---|---|
| — | — | — | *nothing captured yet — steps 3–9 need a browser* | — |

## API vs Dashboard

| Step | Route |
|---|---|
| Seeding plans and subscriptions | **API** (`scripts/seed_razorpay.py`) |
| Creating a payment link | **API** |
| Registering the webhook | **Dashboard only** |
| Authenticating a mandate | **Browser** (subscription `short_url`) |
| Triggering a test charge | **Dashboard only** (SPEC §5.8 #7) |
| Reaching `halted` | **Neither — elapsed time only** |
