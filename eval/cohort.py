"""Synthetic mandate cohort — SPEC §5.10's mix, amounts and category slices.

Every case carries a *true* failure class, assigned here and known only to the
simulator. Salvage never sees it: arms B and C receive a real Razorpay
`error_reason` string sampled from `taxonomy/failure_classes.yaml` for that
class and have to classify it themselves through the actual normaliser. That
separation is the point — a cohort that handed the pipeline its own answer key
would be measuring nothing.

Reason strings come from the committed taxonomy rather than being invented, so
the normaliser is exercised on the same vocabulary Razorpay actually publishes.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from api.normalise.types import FailureClass

TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "taxonomy" / "failure_classes.yaml"

# SPEC §5.10's stated mix. Weights, not counts — the cohort size is a
# parameter, and these are renormalised against whatever size is asked for.
CLASS_MIX: dict[FailureClass, float] = {
    FailureClass.TEMPORARY_LIQUIDITY: 0.40,
    FailureClass.CREDENTIAL_STALE: 0.15,
    FailureClass.CHANNEL_DOWNTIME: 0.10,
    FailureClass.ISSUER_HOLD: 0.08,
    FailureClass.AUTH_ABANDONED: 0.07,
    FailureClass.CREDENTIAL_DEAD: 0.06,
    FailureClass.MANDATE_INVALID: 0.05,
    FailureClass.LIMIT_EXCEEDED: 0.04,
    FailureClass.UNKNOWN: 0.05,
}

# SPEC §5.10: "Amounts log-normal, ₹199—₹95,000, with a deliberate slice above
# the ₹15,000 AFA line and a slice in the insurance/MF category to exercise
# G3's ₹1L branch."
MIN_PAISE = 199_00
MAX_PAISE = 95_000_00
# Chosen so the bulk of the distribution sits in ordinary subscription
# territory (a few hundred to a few thousand rupees) while the tail reaches
# the AFA line often enough to exercise G3 without the cohort becoming a
# high-value outlier set that flatters nobody.
LOGNORMAL_MU = math.log(1_800_00)
LOGNORMAL_SIGMA = 1.15

# The ₹1,00,000 AFA ceiling applies to these categories; everything else gets
# ₹15,000 (PRD §9, G3_AFA). A slice of the cohort lands here on purpose.
ENHANCED_CATEGORIES = ("insurance", "mf", "cc_bill")
ENHANCED_CATEGORY_SHARE = 0.15

UPI_SHARE = 0.35

# A small slice of customers have exercised their permanent opt-out. Real
# cohorts contain them, and a cohort without any would never exercise
# G6_OPTOUT — the gate would look correct because nothing ever asked it.
OPTED_OUT_SHARE = 0.03


@dataclass(frozen=True)
class SyntheticCase:
    """One synthetic mandate. `true_failure_class` is the simulator's input and
    is never shown to Salvage.
    """

    case_ref: str
    subscription_id: str
    customer_id: str
    true_failure_class: FailureClass
    error_reason: str
    error_code: str
    amount_paise: int
    category: str
    mandate_type: str
    first_failure_at: dt.datetime
    opted_out: bool

    def customer_document(self) -> dict[str, Any]:
        """The `customers` row this case's mandate would already have.

        A real subscription cannot exist without a contact on file — Razorpay
        requires one to create the customer — so every synthetic case gets a
        hashed one. Omitting it is not a neutral simplification: G8_POSTDEBIT
        blocks a charge when there is no channel to send the RBI-required
        post-debit confirmation to, so a cohort with no contacts makes every
        charge in the batch structurally impossible and reports Salvage
        recovering nothing. Found by hitting exactly that on the first run.

        The hashes are placeholders, not derived from anything: PRD §8.2
        stores contact and email hashed, and there is no real person here.
        """
        return {
            "_id": self.customer_id,
            "contact_hash": f"sha256:sim:{self.customer_id}:contact",
            "email_hash": f"sha256:sim:{self.customer_id}:email",
            "opt_out_at": (
                self.first_failure_at - dt.timedelta(days=30) if self.opted_out else None
            ),
        }

    def payment_failed_payload(self, payment_id: str) -> dict[str, Any]:
        """A `payment.failed` webhook body of the shape day 1's real captures
        carry, trimmed to the fields the pipeline reads.
        """
        return {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "subscription_id": self.subscription_id,
                        "order_id": f"order_{self.case_ref}",
                        "invoice_id": None,
                        "customer_id": self.customer_id,
                        "amount": self.amount_paise,
                        "currency": "INR",
                        "method": "upi" if self.mandate_type == "upi_autopay" else "card",
                        "error_code": self.error_code,
                        "error_reason": self.error_reason,
                        "error_description": f"simulated {self.true_failure_class.value}",
                    }
                }
            },
        }


def _reasons_by_class() -> dict[FailureClass, list[tuple[str, str]]]:
    """(reason, code) pairs per class, from the committed taxonomy."""
    rules = yaml.safe_load(TAXONOMY_PATH.read_text())["rules"]
    table: dict[FailureClass, list[tuple[str, str]]] = {}
    for rule in rules:
        reason = rule.get("reason")
        if not reason:
            continue  # code-only and description-only fallback rules
        failure_class = FailureClass(rule["class"])
        code = rule.get("code") or "BAD_REQUEST_ERROR"
        table.setdefault(failure_class, []).append((reason, code))

    missing = [c for c in CLASS_MIX if c not in table]
    if missing:
        raise ValueError(
            f"taxonomy/failure_classes.yaml has no reason-keyed rule for "
            f"{[c.value for c in missing]}, so the cohort cannot build a case that "
            f"Salvage would classify into it. Add a rule or drop the class from CLASS_MIX."
        )
    return table


def _counts_for(size: int) -> dict[FailureClass, int]:
    """Largest-remainder apportionment, so the counts sum to exactly `size`
    and no class is silently rounded out of existence.
    """
    exact = {c: size * w for c, w in CLASS_MIX.items()}
    counts = {c: int(v) for c, v in exact.items()}
    shortfall = size - sum(counts.values())
    by_remainder = sorted(exact, key=lambda c: exact[c] - counts[c], reverse=True)
    for failure_class in by_remainder[:shortfall]:
        counts[failure_class] += 1
    return counts


def generate_cohort(size: int, seed: int, run_id: str) -> list[SyntheticCase]:
    """`size` cases, reproducible from `seed`.

    Identifiers are namespaced by `run_id` so two runs can never collide on a
    case identity — `find_or_create_case()` matches on `subscription_id`
    within a run partition, and a shared identifier across runs would let one
    run's history leak into another's gate context.
    """
    if size < len(CLASS_MIX):
        raise ValueError(
            f"cohort size {size} is smaller than the {len(CLASS_MIX)} classes in the mix; "
            f"SPEC §5.10 specifies 30—50 cases and every class needs to be reachable."
        )

    rng = random.Random(seed)
    reasons = _reasons_by_class()
    cases: list[SyntheticCase] = []

    index = 0
    for failure_class, count in _counts_for(size).items():
        for _ in range(count):
            reason, code = rng.choice(reasons[failure_class])
            category = (
                rng.choice(ENHANCED_CATEGORIES)
                if rng.random() < ENHANCED_CATEGORY_SHARE
                else "standard"
            )
            amount = int(
                min(MAX_PAISE, max(MIN_PAISE, rng.lognormvariate(LOGNORMAL_MU, LOGNORMAL_SIGMA)))
            )
            case_ref = f"{run_id}_{index:03d}"
            cases.append(
                SyntheticCase(
                    case_ref=case_ref,
                    subscription_id=f"sub_{case_ref}",
                    customer_id=f"cust_{case_ref}",
                    true_failure_class=failure_class,
                    error_reason=reason,
                    error_code=code,
                    amount_paise=amount,
                    category=category,
                    mandate_type="upi_autopay" if rng.random() < UPI_SHARE else "card",
                    first_failure_at=_first_failure_at(rng),
                    opted_out=rng.random() < OPTED_OUT_SHARE,
                )
            )
            index += 1

    # Interleave, so a run that stops early does not stop having only seen
    # TEMPORARY_LIQUIDITY cases.
    rng.shuffle(cases)
    return cases


def _first_failure_at(rng: random.Random) -> dt.datetime:
    """Spread across a whole month and all 24 hours, in UTC.

    The spread is the point: the day of month drives the payday and pre-payday
    modifiers, and the hour decides whether G1_WINDOW will let a contact go out
    now or replan it to the morning. A cohort clustered at noon on the 10th
    would never exercise either.
    """
    return dt.datetime(
        2026,
        9,
        rng.randint(1, 30),
        rng.randint(0, 23),
        rng.randint(0, 59),
        tzinfo=dt.UTC,
    )
