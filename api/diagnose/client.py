"""diagnose(): the LLM's only job (SPEC §5.3). Diagnoses, never decides money
(invariant 2) — the normaliser's result wins over the LLM's whenever it isn't
UNKNOWN, enforced here, not left to caller discipline.

Not a razorpay-SDK caller, so this module is untouched by
scripts/check_boundaries.py's rule 1 — it talks to Groq over plain HTTP.
"""

from __future__ import annotations

import json

import httpx
from pydantic import ValidationError

from api.config import settings
from api.diagnose.schema import Diagnosis, DiagnosisContext, RootCause
from api.normalise.types import FailureClass, NormaliseResult

# NFR-01: each LLM request capped at 8s, at most two requests per decision
# (the one repair retry). Worst case per decision is ~16s, not 8s — PRD §7
# says this explicitly now (corrected in the same commit, spec-guardian
# review, see NOTES.md's Phase 3 entry).
LLM_TIMEOUT_SECONDS = 8.0

# SPEC §4.1's Retryable column, used only when the LLM did not produce this
# diagnosis (deterministic_override, including DISABLE_LLM) — the normaliser
# itself has no opinion on recoverability, only on class, so this table is
# what supplies PRD §8.2's decisions.diagnosis.is_recoverable field in that
# case. LIMIT_EXCEEDED's SPEC-listed "Conditional" is treated as recoverable
# (True): POL-007 still schedules a retry for it, so a default of "give it a
# retry" matches what the policy engine actually does with the class.
_DETERMINISTIC_RECOVERABLE: dict[FailureClass, bool] = {
    FailureClass.TEMPORARY_LIQUIDITY: True,
    FailureClass.ISSUER_HOLD: True,
    FailureClass.CHANNEL_DOWNTIME: True,
    FailureClass.AUTH_ABANDONED: True,
    FailureClass.LIMIT_EXCEEDED: True,
    FailureClass.CREDENTIAL_STALE: False,
    FailureClass.CREDENTIAL_DEAD: False,
    FailureClass.MANDATE_INVALID: False,
    FailureClass.UNKNOWN: False,
}

# Everything that means "this attempt produced no usable text", broad on
# purpose: an httpx transport/status error, a malformed JSON body, or a
# well-formed body missing the expected choices/message/content shape all
# take the same repair-then-UNKNOWN path — the model, not the caller, is
# responsible for handing back a parseable completion.
_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    httpx.HTTPError,
    json.JSONDecodeError,
    KeyError,
    IndexError,
    TypeError,
)

_SYSTEM_PROMPT = (
    "You are a payments failure diagnosis assistant for an Indian recurring-charge "
    "recovery system. Given the context below, diagnose why a recurring charge "
    "attempt failed. You do not have access to the amount, the customer's name, "
    "contact, or email — diagnose from the failure reason and attempt history alone. "
    "You are diagnosing only; you never choose or authorize a money action. "
    "Respond with ONLY a single JSON object matching the given schema. No prose, "
    "no markdown code fences, no explanation outside the JSON object."
)


def diagnose(
    normalised: NormaliseResult,
    context: DiagnosisContext,
    *,
    disable_llm: bool | None = None,
    client: httpx.Client | None = None,
) -> Diagnosis:
    """`disable_llm` defaults to settings().disable_llm; pass explicitly to
    override (tests do this rather than mutating global settings). `client`
    lets tests inject an httpx.Client(transport=httpx.MockTransport(...)) —
    when omitted, diagnose() opens and closes its own client.
    """
    effective_disable = settings().disable_llm if disable_llm is None else disable_llm
    if effective_disable:
        return _deterministic_only(normalised)

    if client is not None:
        root_cause, raw = _diagnose_via_llm(context, client=client)
    else:
        with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as owned_client:
            root_cause, raw = _diagnose_via_llm(context, client=owned_client)

    return _combine(normalised, root_cause, raw)


def _deterministic_only(normalised: NormaliseResult) -> Diagnosis:
    return Diagnosis(
        failure_class=normalised.failure_class,
        confidence=1.0 if normalised.failure_class is not FailureClass.UNKNOWN else 0.0,
        is_recoverable=_DETERMINISTIC_RECOVERABLE[normalised.failure_class],
        evidence=(),
        timing_hint=None,
        source="deterministic_override",
        llm_raw=None,
    )


def _combine(
    normalised: NormaliseResult, root_cause: RootCause | None, raw: str | None
) -> Diagnosis:
    if normalised.failure_class is not FailureClass.UNKNOWN:
        # Invariant 2: the normaliser wins. The LLM was still called above
        # and `raw` is still stored — the arm-C comparison needs the LLM's
        # opinion recorded even on cases where it didn't drive anything.
        return Diagnosis(
            failure_class=normalised.failure_class,
            confidence=1.0,
            is_recoverable=_DETERMINISTIC_RECOVERABLE[normalised.failure_class],
            evidence=(),
            timing_hint=None,
            source="deterministic_override",
            llm_raw=raw,
        )
    if root_cause is None:
        # Normaliser was UNKNOWN and the LLM failed validation twice, or
        # errored/timed out on both attempts. Still UNKNOWN — never a guess.
        return Diagnosis(
            failure_class=FailureClass.UNKNOWN,
            confidence=0.0,
            is_recoverable=False,
            evidence=("LLM diagnosis unavailable: invalid output after repair, or request failed",),
            timing_hint=None,
            source="llm",
            llm_raw=raw,
        )
    return Diagnosis(
        failure_class=root_cause.failure_class,
        confidence=root_cause.confidence,
        is_recoverable=root_cause.is_recoverable,
        evidence=tuple(root_cause.evidence),
        timing_hint=root_cause.suggested_timing_hint,
        source="llm",
        llm_raw=raw,
    )


def _diagnose_via_llm(
    context: DiagnosisContext, *, client: httpx.Client
) -> tuple[RootCause | None, str | None]:
    prompt = build_prompt(context)
    first_raw: str | None
    try:
        first_raw = _call_llm(prompt, client=client)
    except _TRANSPORT_ERRORS as exc:
        first_raw = None
        return _repair(
            prompt, client=client, first_raw=first_raw, first_error=f"request failed: {exc}"
        )
    try:
        return RootCause.model_validate_json(first_raw), first_raw
    except ValidationError as exc:
        return _repair(prompt, client=client, first_raw=first_raw, first_error=str(exc))


def _repair(
    prompt: str, *, client: httpx.Client, first_raw: str | None, first_error: str
) -> tuple[RootCause | None, str | None]:
    """The one repair retry (SPEC §5.3 rule 3: "Log both attempts"). Both raw
    outputs are preserved in the returned string — PRD §8.2's `llm_raw` is a
    single field, so both attempts are joined into it rather than only the
    last one surviving. `None` for either attempt means the request itself
    never returned text (transport failure), distinct from text that failed
    validation — `_join_attempts` renders that distinction rather than
    silently dropping it.
    """
    second_raw: str | None
    try:
        second_raw = _call_llm(prompt, client=client, repair_of=(first_raw or "", first_error))
    except _TRANSPORT_ERRORS as exc:
        second_raw = None
        return None, _join_attempts(first_raw, first_error, second_raw, f"request failed: {exc}")
    try:
        return RootCause.model_validate_json(second_raw), _join_attempts(
            first_raw, first_error, second_raw, None
        )
    except ValidationError:
        return None, _join_attempts(first_raw, first_error, second_raw, None)


def _join_attempts(
    first_raw: str | None, first_error: str, second_raw: str | None, second_error: str | None
) -> str | None:
    """SPEC §5.3 rule 3: both attempts, logged verbatim (PRD §8.2's `llm_raw`
    comment: "verbatim, even when rejected"). Returns None only when neither
    attempt ever produced any completion text at all — there is nothing to
    log verbatim in that case, matching the pre-repair behaviour for a
    single failed call.
    """
    if first_raw is None and second_raw is None:
        return None
    return (
        f"[attempt 1] {first_raw if first_raw is not None else f'(no response: {first_error})'}\n"
        f"[repair reason] {first_error}\n"
        f"[attempt 2] "
        f"{second_raw if second_raw is not None else f'(no response: {second_error})'}"
    )


def build_prompt(context: DiagnosisContext) -> str:
    """No PII: only the fields DiagnosisContext carries reach this string —
    there is no amount/name/contact/email field on that type to render.
    """
    lines = [
        _SYSTEM_PROMPT,
        "",
        f"failure_reason: {context.failure_reason!r}",
        f"attempt_count: {context.attempt_count}",
        f"attempt_timestamps: {[t.isoformat() for t in context.attempt_timestamps]}",
        f"prior_failure_classes: {[c.value for c in context.prior_failure_classes]}",
        f"mandate_type: {context.mandate_type!r}",
        "prior_successful_charge_dates: "
        f"{[d.isoformat() for d in context.prior_successful_charge_dates]}",
        "",
        "JSON schema to match exactly:",
        json.dumps(RootCause.model_json_schema()),
    ]
    return "\n".join(lines)


def _call_llm(
    prompt: str, *, client: httpx.Client, repair_of: tuple[str, str] | None = None
) -> str:
    cfg = settings()
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    if repair_of is not None:
        bad_output, error = repair_of
        messages.append({"role": "assistant", "content": bad_output})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"That response was not valid: {error}. Respond again with ONLY the "
                    f"corrected JSON object, matching the schema exactly."
                ),
            }
        )
    response = client.post(
        f"{cfg.llm_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
        json={
            "model": cfg.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
    )
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    return str(content)
