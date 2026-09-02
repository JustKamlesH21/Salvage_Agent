"""Explicit arm state for going live. CLAUDE.md invariant 8: "DRY_RUN
defaults true. Going live requires an explicit arm call with a confirm
token."

Found unenforced by compliance-auditor's Day 4 review: nothing previously
checked a confirm token at all — setting `DRY_RUN=false` in the environment
was already sufficient to reach `execute_action()`'s real-dispatch branch,
and `Makefile`/`README.md` both claimed an arm requirement that no code
enforced. `execute_action()` now refuses the live-dispatch branch unless
`is_armed()` — this module is the whole enforcement surface.

Process-local and intentionally simple: no `/control/arm` HTTP route exists
yet (that lands with the rest of the control API, PRD §7 — out of this
phase's scope), so this is the enforcement point that route will call into
once it exists. A module-level flag is deliberate, not an oversight — arming
is a global operational state, the same shape as the kill switch
(`settings().kill_switch`), not a per-request one.
"""

from __future__ import annotations

from api.config import settings

_armed = False


def arm(confirm_token: str) -> bool:
    """Arms only if `confirm_token` matches `settings().arm_confirm_token`
    exactly, and that setting is itself non-empty — an unset token must never
    be satisfied by an empty string argument.
    """
    global _armed
    expected = settings().arm_confirm_token
    if not expected or confirm_token != expected:
        return False
    _armed = True
    return True


def disarm() -> None:
    global _armed
    _armed = False


def is_armed() -> bool:
    return _armed
