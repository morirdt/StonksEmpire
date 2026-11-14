"""The scheduler wiring: which jobs exist, and the rules every job shares.

The jobs themselves are tested by calling them (``tests/integration/test_jobs.py``).
This only checks what ``build_scheduler`` registers, without starting it.
"""

from __future__ import annotations

from apscheduler.triggers.interval import IntervalTrigger

from app.jobs.worker import build_scheduler


def test_every_job_is_registered() -> None:
    ids = {job.id for job in build_scheduler().get_jobs()}

    assert ids == {
        "poll_quotes",
        "poll_quotes_closing",
        "ingest_eod",
        "refresh_symbols",
        "prune_notifications",
        "heartbeat",
    }


def test_no_job_can_pile_up_or_overlap_itself() -> None:
    for job in build_scheduler().get_jobs():
        assert job.coalesce is True, job.id
        assert job.max_instances == 1, job.id


def test_a_missed_interval_run_is_dropped_before_the_next_one_is_due() -> None:
    """A grace window as long as the interval would let a late run collide with the next."""
    for job in build_scheduler().get_jobs():
        if isinstance(job.trigger, IntervalTrigger):
            interval = job.trigger.interval.total_seconds()
            assert job.misfire_grace_time < interval, job.id


def test_the_scheduler_runs_on_market_time() -> None:
    assert str(build_scheduler().timezone) == "America/New_York"
