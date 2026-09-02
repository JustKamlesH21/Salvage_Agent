"""The global kill switch — FR-10, CLAUDE.md invariant 4.

Configuration (`KILL_SWITCH=true`) is immutable for the life of the process,
so engaging the switch at runtime needs somewhere to live that both the
decision pipeline and the HTTP control route can see. This is that place, and
it sits next to `arm.py` for the same reason: safety state belongs with the
executor, not in a route module that core code would then have to import
backwards.

Engaged means engaged. There is no argument, flag or config value that
un-engages it for a particular case, and `release()` exists only so a test or
an operator can deliberately stand the system back up — it is never called on
a decision path.
"""

from __future__ import annotations

from api.config import settings

_engaged_at_runtime = False


def engage() -> None:
    global _engaged_at_runtime
    _engaged_at_runtime = True


def release() -> None:
    global _engaged_at_runtime
    _engaged_at_runtime = False


def engaged() -> bool:
    """True if the switch is engaged either way round.

    Configuration is checked on every call rather than cached: a process
    started with `KILL_SWITCH=true` must stay frozen even if something calls
    `release()`, because the operator who set the environment variable is
    making a stronger statement than a runtime toggle can undo.
    """
    return _engaged_at_runtime or settings().kill_switch
