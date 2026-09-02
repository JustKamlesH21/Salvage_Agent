"""Shared gate types. Not a gate itself — exempt from
scripts/check_clauses.py's CLAUSE_REF/RULE_TEXT requirement (NON_GATE_MODULES).

Every gate evaluates a `GateContext` built fresh per call. Nothing here is
cached from schedule time — that is what makes fire-time re-evaluation
possible (invariant 5): call `evaluate_chain()` twice with two different
`now_utc` values and nothing in between remembers the first answer.

"Fail closed on malformed input" (gate-authoring skill, mandatory test #6) is
each gate's own responsibility, not enforced centrally here — a gate that
delegates its own defensiveness to a shared validator is one bug away from
every gate failing open together.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo

from api.policy.types import Action

IST = ZoneInfo("Asia/Kolkata")

# SPEC §4.2's catalog, split by what each action means for gate purposes.
# Both frozensets, not gates — exempt like this module itself.
CONTACT_ACTIONS = frozenset(
    {Action.SEND_PRE_DEBIT_NOTICE, Action.CREATE_REAUTH_LINK, Action.CREATE_AFA_LINK, Action.NUDGE}
)
CHARGE_ACTIONS = frozenset({Action.RETRY_NOW, Action.RETRY_SCHEDULED})
# Always permitted even under an active opt-out (G6) — these are the
# "stop bothering them" terminal actions, not further contact.
OPT_OUT_EXEMPT_ACTIONS = frozenset({Action.CLOSE_NO_ACTION, Action.SUPPRESS})

KNOWN_CATEGORIES = frozenset({"standard", "insurance", "mf", "cc_bill"})
KNOWN_SUBSCRIPTION_STATES = frozenset(
    {"created", "authenticated", "active", "pending", "halted", "cancelled", "completed", "expired"}
)
KNOWN_CONTACT_TARGETS = frozenset({"account_holder", "third_party"})


class VerdictKind(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REPLAN = "REPLAN"


@dataclass(frozen=True)
class GateVerdict:
    """One gate's answer. `clause_ref` is always the gate's own CLAUSE_REF,
    even on ALLOW — the Decision Tape can choose not to render it there, but
    the record of which clause was checked is complete either way.
    """

    gate: str
    kind: VerdictKind
    clause_ref: str
    detail: str
    evaluated_at: dt.datetime  # UTC (invariant 10)
    replan_at: dt.datetime | None = None  # UTC, set only when kind is REPLAN


@dataclass(frozen=True)
class GateContext:
    """Everything the eleven gates need, evaluated fresh every time. Built by
    api/pipeline.py for a live decision cycle, or directly by tests.
    """

    now_utc: dt.datetime
    action: Action
    kill_switch_engaged: bool
    amount_paise: int
    category: str
    mandate_type: str
    subscription_state: str
    salvage_attempts_in_window: tuple[dt.datetime, ...]  # UTC; G4's 14-day rolling window
    contacts_in_7d: tuple[dt.datetime, ...]  # UTC; G5's 7-day contact count
    last_contact_at: dt.datetime | None  # UTC; G5's 48h cooldown
    pre_debit_notice_sent_at: dt.datetime | None  # UTC; G2's ≥24h notice
    opted_out: bool
    contact_target: str  # "account_holder" | "third_party" — G7
    has_confirmation_channel: bool  # G8
    idempotency_key: str  # G10
    idempotency_key_exists: bool  # G10 — precomputed by the caller (one audit read, not eleven)

    def now_ist(self) -> dt.datetime:
        return self.now_utc.astimezone(IST)
