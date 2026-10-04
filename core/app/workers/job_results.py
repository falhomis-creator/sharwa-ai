"""core/app/workers/job_results.py - the scheduler handler contract (H89/H92).

A handler's verdict about ONE claimed job, computed inside the job's tenant
transaction: Done (effects applied, finish the job), Defer(run_at) (the world
changed - re-run later WITHOUT consuming an attempt, H88), or Cancel(reason)
(a permanent condition - the job ends 'cancelled' with the reason recorded).

Kept in its own module so handler modules (app/workers/cart_reminder.py, ...)
and the engine (app/workers/scheduler.py) can both import it with no import
cycle. The payload tells the handler WHY the job exists; the handler re-reads
the world at execution time and decides (H76 extended to the action itself).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Done:
    """Effects committed in this transaction; mark the job done."""


@dataclass(frozen=True)
class Defer:
    """Not actionable yet (or the state changed); re-run at run_at. The claim's
    attempts increment is rolled back - a defer is NOT an attempt (H88)."""

    run_at: datetime


@dataclass(frozen=True)
class Cancel:
    """A permanent condition makes this job pointless; end it 'cancelled'."""

    reason: str
