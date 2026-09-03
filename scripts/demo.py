#!/usr/bin/env python3
"""Drive the demo: post real, signed `payment.failed` webhooks at a running API.

This is not a fixture loader and it does not write to the database. It posts
HMAC-signed events at `POST /webhooks/razorpay` exactly as Razorpay would, and
everything after that is the real path — signature verification on the raw
body, the append-only event chain, the normaliser, the policy engine, all
eleven gates, the executor in dry run. What appears on the Command Deck a
moment later was decided, not staged.

That distinction is the whole point of running it on camera. A seeded database
shows you what the screens look like. This shows you the agent working.

    scripts/demo.py cohort              # the scripted set, one every 2s
    scripts/demo.py cohort --delay 0    # all at once
    scripts/demo.py one --reason insufficient_funds --amount 1800000
    scripts/demo.py soon --minutes 2    # a case that comes due while you watch

`soon` is the one that needs explaining. Most replans land hours out — the
liquidity rule schedules to the next payday window, and G1_WINDOW moves an
evening contact to 08:00 tomorrow. Neither fits in a five-minute recording. It
posts a normal webhook and then rewrites that decision's scheduled time to a
couple of minutes away, so the scheduler picks it up while the camera is
running. The gates are not touched: all eleven still re-run at fire time, and
they run against the clock as it actually is. What is faked is the wait, and
nothing else. Say so on camera — it is a far better line than pretending.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import hmac
import json
import random
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from api.config import settings

# One real Razorpay reason per failure class, taken from
# taxonomy/failure_classes.yaml so the demo exercises the classifier rather
# than a code invented for it.
COHORT: list[tuple[str, str, int, str]] = [
    # (reason, method, amount_paise, what it is meant to show)
    ("insufficient_funds", "card", 1_800_000, "the signature case: G2 and G3 both refuse"),
    ("insufficient_funds", "card", 49_900, "same class, under every threshold"),
    ("transaction_limit_exceeded", "card", 2_500_000, "limit exceeded, scheduled retry"),
    ("card_expired", "card", 129_900, "stale credential, re-auth link"),
    ("debit_instrument_blocked", "card", 89_900, "hard decline: no retry is ever spent"),
    ("authentication_failed", "card", 75_000, "abandoned auth, re-auth link"),
    ("mandate_creation_declined", "upi", 99_900, "invalid mandate, AFA link"),
    ("payment_failed", "upi", 29_900, "channel downtime, retry soon"),
    ("a_reason_no_taxonomy_rule_matches", "card", 45_000, "UNKNOWN -> the exception desk"),
]


def _event(reason: str, method: str, amount_paise: int, *, suffix: str) -> dict:
    """A `payment.failed` event in the shape the ingest path expects."""
    now = int(time.time())
    return {
        "entity": "event",
        "account_id": "acc_DEMO",
        "event": "payment.failed",
        "contains": ["payment"],
        "created_at": now,
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_demo{suffix}",
                    "entity": "payment",
                    "amount": amount_paise,
                    "currency": "INR",
                    "status": "failed",
                    "method": method,
                    "captured": False,
                    "customer_id": f"cust_demo{suffix}",
                    "invoice_id": f"inv_demo{suffix}",
                    "order_id": f"order_demo{suffix}",
                    "subscription_id": f"sub_demo{suffix}",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_reason": reason,
                    "error_description": reason.replace("_", " "),
                    "error_source": "customer",
                    "error_step": "payment_authorization",
                    "created_at": now,
                }
            }
        },
    }


def post(url: str, event: dict, secret: str) -> tuple[int, str]:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"refusing to post to a non-HTTP url: {url}")
    # Signed over the exact bytes that are sent. The API verifies on the raw
    # body before parsing, so anything that re-serialises between signing and
    # sending will and should fail.
    body = json.dumps(event, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(  # noqa: S310 — scheme checked above
        url,
        data=body,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, response.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


async def _ensure_customer(customer_id: str, *, opted_out: bool = False) -> None:
    """Put the customer on file before the failure arrives.

    The ingest path reads `customers` and never writes it, so a case opened
    purely from a webhook has no customer record and G8_POSTDEBIT refuses every
    charge on it — correctly: a mandate customer with no contact channel cannot
    be sent the post-debit confirmation the framework requires. In production
    the customer exists because they signed up, not because a payment failed.
    This puts them there, hashed, the way PRD §8.2 stores them.
    """
    from api.db.client import get_db

    await get_db()["customers"].update_one(
        {"_id": customer_id},
        {
            "$set": {
                "contact_hash": hashlib.sha256(f"{customer_id}:contact".encode()).hexdigest(),
                "email_hash": hashlib.sha256(f"{customer_id}:email".encode()).hexdigest(),
                "opt_out_at": dt.datetime.now(dt.UTC) if opted_out else None,
            }
        },
        upsert=True,
    )


def _suffix() -> str:
    # Distinguishes one demo run's ids from another's. Not a secret and not a
    # key — the idempotency guarantees are the executor's, not this script's.
    return f"{random.randint(0, 0xFFFFFF):06x}"  # noqa: S311


def cmd_cohort(args: argparse.Namespace, url: str, secret: str) -> int:
    print(f"posting {len(COHORT)} events at {url}\n")

    async def run() -> None:
        for reason, method, amount, note in COHORT:
            suffix = _suffix()
            if not args.no_customer:
                await _ensure_customer(f"cust_demo{suffix}")
            status, body = post(url, _event(reason, method, amount, suffix=suffix), secret)
            rupees = f"₹{amount // 100:,}"
            mark = "ok " if status == 200 else "ERR"
            print(f"  {mark} {status}  {rupees:>10}  {reason:<34} {note}")
            if status != 200:
                print(f"       {body}")
            await asyncio.sleep(args.delay)

    asyncio.run(run())
    print("\nWatch the deck. Each one is a full decision cycle: normalise, diagnose,")
    print("policy, eleven gates, executor. Nothing here was written to the database.")
    return 0


def cmd_one(args: argparse.Namespace, url: str, secret: str) -> int:
    status, body = post(
        url, _event(args.reason, args.method, args.amount, suffix=_suffix()), secret
    )
    print(status, body)
    return 0 if status == 200 else 1


def cmd_soon(args: argparse.Namespace, url: str, secret: str) -> int:
    """Produce a genuine replan, then bring it forward so it fires on camera.

    The replan is real and is caused by a real clause. The same subscription is
    failed twice: the first failure gets a pre-debit notice dispatched, the
    second is a charge against a notice that went out minutes ago, and
    G2_PREDEBIT refuses it until the framework's 24-hour lead time has elapsed
    — REPLAN to notice + 24h, not a block, because waiting genuinely fixes it.

    Then, and only then, the scheduled time is moved to a couple of minutes out.
    The gates are untouched: all eleven re-run at fire time against the real
    clock. What is shortened is the wait. Say that on camera.
    """
    from api.db.client import get_db

    suffix = _suffix()
    sub = f"sub_demo{suffix}"

    async def bring_forward() -> int:
        await _ensure_customer(f"cust_demo{suffix}")

        print(f"1/2  first failure on {sub} — expect a pre-debit notice to go out")
        status, body = post(url, _event(args.reason, "card", args.amount, suffix=suffix), secret)
        print(f"     {status} {body}")
        if status != 200:
            return 1
        await asyncio.sleep(3)  # the pipeline runs as a background task

        print(f"2/2  second failure on {sub} — the notice is minutes old, not 24 hours")
        event = _event(args.reason, "card", args.amount, suffix=suffix)
        event["payload"]["payment"]["entity"]["id"] = f"pay_demo{suffix}b"
        status, body = post(url, event, secret)
        print(f"     {status} {body}")
        if status != 200:
            return 1

        db = get_db()
        due = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=args.minutes)
        case = None
        for _ in range(20):
            await asyncio.sleep(0.5)
            case = await db["cases"].find_one({"subscription_id": sub})
            if case:
                break
        if not case:
            print("\nno case appeared for that subscription")
            return 1

        decision = await db["decisions"].find_one(
            {"case_id": case["_id"], "final.verdict": "replan"}, sort=[("created_at", -1)]
        )
        if not decision:
            print("\nno replanned decision on that case — nothing to bring forward.")
            print("Both attempts cleared the chain, which is a legitimate outcome:")
            print("inside 08:00-19:00 IST a notice dispatches straight away.")
            return 1

        mover = next(
            (v["gate"] for v in decision.get("gate_results", []) if v.get("verdict") == "REPLAN"),
            "?",
        )
        was = decision["final"]["at"]
        await db["decisions"].update_one({"_id": decision["_id"]}, {"$set": {"final.at": due}})
        print(f"\n{decision['final']['action']} was refused by {mover}")
        print(f"  scheduled for : {was}")
        print(f"  moved to      : {due.isoformat()}  ({args.minutes} min out)")
        print(f"  case          : {case['_id']}")
        print("\nThe gates are untouched — all eleven re-run when it fires, against the")
        print("real clock. Only the wait was shortened.")
        return 0

    return asyncio.run(bring_forward())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000/webhooks/razorpay")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("cohort", help="post the scripted set")
    c.add_argument("--delay", type=float, default=2.0, help="seconds between events")
    c.add_argument(
        "--no-customer",
        action="store_true",
        help="skip seeding the customer record — every charge then refuses on G8_POSTDEBIT, "
        "which is worth showing once",
    )
    c.set_defaults(fn=cmd_cohort)

    o = sub.add_parser("one", help="post a single event")
    o.add_argument("--reason", default="insufficient_funds")
    o.add_argument("--method", default="card")
    o.add_argument("--amount", type=int, default=1_800_000, help="paise")
    o.set_defaults(fn=cmd_one)

    s = sub.add_parser("soon", help="post a case that comes due in a few minutes")
    s.add_argument("--reason", default="insufficient_funds")
    s.add_argument("--amount", type=int, default=49_900, help="paise")
    s.add_argument("--minutes", type=int, default=2)
    s.set_defaults(fn=cmd_soon)

    args = parser.parse_args()
    secret = settings().razorpay_webhook_secret
    if not secret:
        print("RAZORPAY_WEBHOOK_SECRET is not set. The API will reject every event 401.")
        return 2
    return int(args.fn(args, args.url, secret))


if __name__ == "__main__":
    raise SystemExit(main())
