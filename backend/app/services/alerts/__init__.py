"""Alerts: a one-symbol screen with a schedule, or a watch on a saved screen.

* ``evaluation`` — the edge rules, as pure functions.
* ``service`` — CRUD and trigger history, scoped to the caller.
* ``firing`` — evaluating alerts inside the worker's jobs and recording what
  fired, in the job's transaction.

The evaluation queries live in ``app/repositories/alert_repository.py``. They
reuse the screener's compiler and universe, because a second evaluator would
mean a second set of null semantics.
"""

from __future__ import annotations
