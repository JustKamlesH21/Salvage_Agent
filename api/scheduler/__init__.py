"""Fire-time gate re-evaluation. SPEC §5.5, CLAUDE.md invariant 5.

Two modules, and the split is the point.

`fire_due_jobs()` (runner) is the pure core: given jobs, a clock, a way to
build a gate context and a way to execute, it re-evaluates every due job
against the context as it is *now* and never against one cached at schedule
time. It touches no database and no configuration, which is why it can be
tested exhaustively.

`tick()` (live) is the adapter that gives it a real world: due jobs read back
out of `decisions`, a gate context built from storage, the real executor, and
the decision each fire produces written back. `scheduler()` runs it on an
interval and is off unless `SCHEDULER_TICK_SECONDS` says otherwise — a process
that dispatches money actions on a timer should be something you turned on.
"""

from __future__ import annotations

from api.scheduler.live import due_jobs, scheduler, tick
from api.scheduler.runner import fire_due_jobs
from api.scheduler.types import FireResult, ScheduledJob

__all__ = [
    "FireResult",
    "ScheduledJob",
    "due_jobs",
    "fire_due_jobs",
    "scheduler",
    "tick",
]
