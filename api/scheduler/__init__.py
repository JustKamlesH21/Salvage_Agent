"""Fire-time gate re-evaluation. SPEC §5.5, CLAUDE.md invariant 5.

`fire_due_jobs()` is the pure, tested core: a job scheduled at a compliant
moment and fired at a non-compliant one is re-evaluated against the *current*
gate context, not one cached from schedule time. A live APScheduler thread
calling this on a tick is out of scope for this phase (see NOTES.md).
"""

from __future__ import annotations

from api.scheduler.runner import fire_due_jobs
from api.scheduler.types import FireResult, ScheduledJob

__all__ = ["FireResult", "ScheduledJob", "fire_due_jobs"]
