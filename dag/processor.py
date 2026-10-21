"""The DAG processor thread's process-wide state and mutation guard.

A leaf module: it imports nothing from ``dag`` at runtime, so both the
thread's owner (``dag.scheduler``) and the single-writer guard's callers
(``dag.persistence``, ``dag.derived_node``, ``dag.user_input_node``) can
depend on it without a cycle.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dag.scheduler import AsyncRefreshQueue


class ProcessorRuntime:
    """The DAG processor thread's process-wide state: the background
    thread, its private loop and drained queue, and the in-flight
    submission gauge. ONE owner — the processor functions and
    ``AsyncRefreshQueue._enqueue`` read and write it through this class
    (the queue and loop are bound to the processor's thread by
    construction)."""

    thread: threading.Thread | None = None
    loop: asyncio.AbstractEventLoop | None = None
    sched: AsyncRefreshQueue | None = None
    task: asyncio.Task | None = None
    # Queued submissions not yet applied.  ``stop_processor`` drains the
    # scheduler queue AND this count: a submission in flight (or one whose
    # cascade has not been enqueued yet) must land before the process exits,
    # or a write the user already saw confirmed would vanish.
    pending_submissions: int = 0
    submissions_lock = threading.Lock()
    # The yield granted to an in-flight submission between drain slices —
    # a responsive idle decision without a busy spin.
    DRAIN_YIELD = timedelta(seconds=0.01)


def current_processor_thread() -> threading.Thread | None:
    """The processor thread once running; None in tests, lifespan-less
    scripts and startup — single-threaded contexts where mutation is safe
    by definition."""
    return ProcessorRuntime.thread


def assert_mutation_allowed() -> None:
    """Guard: DAG state mutates only on the processor thread.

    Fires only when the processor EXISTS and the caller is not it — the
    one dangerous case. Startup, scripts and tests never start a
    processor, so they are exempt by construction.
    """
    processor = current_processor_thread()
    if processor is not None and threading.current_thread() is not processor:
        raise RuntimeError(
            "DAG mutation off the processor thread — enqueue work instead "
            "(await run_on_processor(...) from request handlers). See "
            "docs/dag-library.md → 'Thread rules'."
        )
