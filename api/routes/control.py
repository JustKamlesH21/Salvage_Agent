"""Safety controls and the posture the status bar reads — SPEC §7, §6.2.

**What arming can and cannot do here.** `POST /control/arm` calls the real
`api.executor.arm.arm()` and requires the real confirm token (invariant 8).
It still cannot move money, and that is by construction rather than by
promise:

  1. `DRY_RUN` is read from the environment at process start and there is no
     HTTP route that changes it. Arming does not flip it.
  2. `execute_action()` reaches a live dispatch only when `dry_run` is false
     *and* the process is armed — both, not either.
  3. `api/executor/client.py`'s `dispatch()` is an unimplemented stub. Live
     Razorpay execution (FR-20) is out of scope for this submission, so the
     live branch raises rather than charging anyone.

So arming is honest about what it is: it flips the posture the UI reports, on
a system whose live path is deliberately not built. Anything else would mean
wiring a route to real money on the last day of a three-day build, which is
not a trade this project is willing to make.

`POST /control/kill` engages the kill switch, which is purely restrictive —
G0_KILL is evaluated first in every chain, and the executor refuses before its
pre-audit row. Nothing about it needs a token, because nothing about stopping
needs protecting.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from api.config import settings
from api.executor.arm import arm, disarm, is_armed
from api.executor.killswitch import engage, engaged, release

router = APIRouter(prefix="/control", tags=["control"])


def kill_switch_engaged() -> bool:
    """Re-exported so api/main.py's /health reports the same switch the
    decision pipeline actually reads.
    """
    return engaged()


class ArmRequest(BaseModel):
    confirm_token: str


class KillRequest(BaseModel):
    engaged: bool = True


@router.get("/status")
async def status() -> dict[str, Any]:
    """What the persistent chrome renders. A judge should never be unsure
    which mode they are watching (SPEC §6.2).
    """
    cfg = settings()
    return {
        "dry_run": cfg.dry_run,
        "armed": is_armed(),
        "kill_switch": kill_switch_engaged(),
        "live_dispatch_available": False,
        "live_dispatch_note": (
            "Live Razorpay dispatch (FR-20) is out of scope for this submission — "
            "api/executor/client.py's dispatch() is an unimplemented stub. Arming "
            "changes the reported posture; it does not create a path to real money."
        ),
        "compliance_tz": cfg.compliance_tz,
        "storage_tz": cfg.tz,
    }


@router.post("/arm")
async def arm_executor(request: ArmRequest) -> dict[str, Any]:
    if not arm(request.confirm_token):
        return {
            "armed": False,
            "error": (
                "Confirm token does not match ARM_CONFIRM_TOKEN. Set it in the "
                "environment and send the same value to arm."
            ),
        }
    return {"armed": True, "dry_run": settings().dry_run}


@router.post("/disarm")
async def disarm_executor() -> dict[str, Any]:
    disarm()
    return {"armed": False}


@router.post("/kill")
async def set_kill_switch(request: KillRequest) -> dict[str, Any]:
    if request.engaged:
        engage()
    else:
        release()
    return {"kill_switch": kill_switch_engaged()}
