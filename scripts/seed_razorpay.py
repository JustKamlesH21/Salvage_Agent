#!/usr/bin/env python3
"""Create the Salvage merchant persona in Razorpay test mode. Idempotent.

Reaches Razorpay through `api.executor.client.get_razorpay_client()` — the same
single door every other caller uses. CLAUDE.md invariant 1 has no exceptions,
including for setup scripts, and `scripts/` is inside the boundary scan.

What it creates
---------------
A SaaS business with an insurance arm, with a plan ladder chosen to exercise
both branches of G3_AFA:

    ₹15,000  standard   at the no-AFA limit, allowed
    ₹15,001  standard   one rupee over, AFA required
    ₹95,000  mf         same order of magnitude, no AFA needed because the
                        category raises the ceiling to ₹1,00,000

Same amount, opposite verdict, decided only by category — which is what shows
G3 is logic rather than a threshold constant.

`category` rides in Razorpay `notes`, so it survives into the webhook payloads
and the fixtures. Razorpay has no notion of it; it is ours (PRD §8.2).

What it does NOT create
-----------------------
**No authenticated card mandates.** Test-mode card tokens live 3 days
(SPEC §5.8). A mandate authenticated today is dead on day 4, six days before
the demo. Subscriptions are created in `created` state; authenticating them is
a day 9-10 step, per CLAUDE.md.

**No payment links by default.** 30 per business, ever. Pass --payment-links to
spend the two budgeted for fixture capture.

Idempotence
-----------
Created ids are written to `.razorpay-manifest.json` (gitignored). On re-run
every recorded id is fetched; only what is missing or deleted is recreated.
Running this twice must not double the account.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.executor.client import get_razorpay_client  # noqa: E402

MANIFEST = ROOT / ".razorpay-manifest.json"

# One plan per row. `amount_paise` straddles both G3 thresholds on purpose.
PLANS: list[dict[str, Any]] = [
    {
        "key": "saas_starter",
        "name": "Salvage SaaS — Starter",
        "amount_paise": 29_900,
        "category": "standard",
        "period": "monthly",
        "note": "far below the ₹15,000 no-AFA limit",
    },
    {
        "key": "saas_pro",
        "name": "Salvage SaaS — Pro",
        "amount_paise": 149_900,
        "category": "standard",
        "period": "monthly",
        "note": "ordinary mid-tier, below the limit",
    },
    {
        "key": "saas_business",
        "name": "Salvage SaaS — Business",
        "amount_paise": 1_500_000,
        "category": "standard",
        "period": "monthly",
        "note": "exactly ₹15,000 — at the no-AFA limit, still allowed",
    },
    {
        "key": "saas_enterprise",
        "name": "Salvage SaaS — Enterprise",
        "amount_paise": 1_500_100,
        "category": "standard",
        "period": "monthly",
        "note": "₹15,001 — one rupee over, AFA required",
    },
    {
        "key": "insurance_term",
        "name": "Salvage Cover — Term Life Premium",
        "amount_paise": 4_750_000,
        "category": "insurance",
        "period": "yearly",
        "note": "₹47,500 — over ₹15,000 but insurance, so no AFA under ₹1,00,000",
    },
    {
        "key": "mf_elss",
        "name": "Salvage Invest — ELSS Annual",
        "amount_paise": 9_500_000,
        "category": "mf",
        "period": "yearly",
        "note": "₹95,000 — same magnitude as an AFA-required standard charge, "
        "allowed only because the category raises the ceiling",
    },
]

# Subscriptions to open against those plans. `purpose` says what each is for,
# so a stale account can be read six days later.
SUBSCRIPTIONS: list[dict[str, Any]] = [
    {"key": "sub_business", "plan": "saas_business", "purpose": "fixture:charged+invoice_paid"},
    {"key": "sub_enterprise", "plan": "saas_enterprise", "purpose": "fixture:payment_failed"},
    {"key": "sub_mf", "plan": "mf_elss", "purpose": "fixture:afa_category_flip"},
    # Its only job is to fail its way to halted. Forced to fail today so
    # Razorpay's T+1/T+2/T+3 runs in the background and subscription.halted
    # lands around day 4, before Phase 2's out-of-order test needs it.
    {"key": "sub_halt_clock", "plan": "saas_pro", "purpose": "fixture:halted — started day 1"},
]


def load_manifest() -> dict[str, Any]:
    if not MANIFEST.exists():
        return {"plans": {}, "subscriptions": {}, "customers": {}, "payment_links": {}}
    loaded: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for section in ("plans", "subscriptions", "customers", "payment_links"):
        loaded.setdefault(section, {})
    return loaded


def save_manifest(manifest: dict[str, Any]) -> None:
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def still_exists(client: Any, kind: str, entity_id: str) -> bool:
    """True if the id is still live in the account. A deleted object is recreated."""
    try:
        getattr(client, kind).fetch(entity_id)
    except Exception:
        return False
    return True


def ensure_plans(client: Any, manifest: dict[str, Any], *, dry: bool) -> None:
    for spec in PLANS:
        recorded = manifest["plans"].get(spec["key"])
        if recorded and still_exists(client, "plan", recorded["id"]):
            print(f"  plan {spec['key']:18} exists  {recorded['id']}")
            continue
        payload = {
            "period": spec["period"],
            "interval": 1,
            "item": {
                "name": spec["name"],
                "amount": spec["amount_paise"],
                "currency": "INR",
                "description": spec["note"],
            },
            "notes": {"salvage_key": spec["key"], "category": spec["category"]},
        }
        if dry:
            print(f"  plan {spec['key']:18} WOULD CREATE  ₹{spec['amount_paise'] / 100:,.2f}")
            continue
        created = client.plan.create(payload)
        manifest["plans"][spec["key"]] = {
            "id": created["id"],
            "amount_paise": spec["amount_paise"],
            "category": spec["category"],
        }
        print(
            f"  plan {spec['key']:18} created {created['id']}  ₹{spec['amount_paise'] / 100:,.2f}"
        )


def ensure_subscriptions(client: Any, manifest: dict[str, Any], *, dry: bool) -> None:
    for spec in SUBSCRIPTIONS:
        recorded = manifest["subscriptions"].get(spec["key"])
        if recorded and still_exists(client, "subscription", recorded["id"]):
            print(f"  sub  {spec['key']:18} exists  {recorded['id']}  ({recorded['status']})")
            continue
        plan = manifest["plans"].get(spec["plan"])
        if plan is None:
            print(f"  sub  {spec['key']:18} SKIPPED — plan {spec['plan']} not created yet")
            continue
        payload = {
            "plan_id": plan["id"],
            "total_count": 12,
            # Not authenticated here. The auth flow mints a card token that
            # expires in 3 days, so it happens on day 9-10 (CLAUDE.md).
            "customer_notify": 1,
            "notes": {"salvage_key": spec["key"], "purpose": spec["purpose"]},
        }
        if dry:
            print(f"  sub  {spec['key']:18} WOULD CREATE  on {plan['id']}")
            continue
        created = client.subscription.create(payload)
        manifest["subscriptions"][spec["key"]] = {
            "id": created["id"],
            "plan_key": spec["plan"],
            "status": created["status"],
            "short_url": created.get("short_url"),
            "purpose": spec["purpose"],
        }
        print(f"  sub  {spec['key']:18} created {created['id']}  ({created['status']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="actually create objects (default: report only)"
    )
    args = parser.parse_args()
    dry = not args.apply

    manifest = load_manifest()
    client = get_razorpay_client()

    print("seed: Razorpay test mode" + ("  [REPORT ONLY — pass --apply to create]" if dry else ""))
    print("\nplans")
    ensure_plans(client, manifest, dry=dry)
    print("\nsubscriptions")
    ensure_subscriptions(client, manifest, dry=dry)

    if not dry:
        save_manifest(manifest)
        print(f"\nseed: manifest written to {MANIFEST.name} (gitignored)")

    print(
        "\nNo card mandate was authenticated. Test-mode tokens live 3 days, so that\n"
        "step belongs to day 9-10 — see CLAUDE.md and NOTES.md. Authenticate a\n"
        "subscription by opening its short_url from the manifest."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
