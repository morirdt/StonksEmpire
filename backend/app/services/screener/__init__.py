"""The screener: a filter tree in, a set of matching symbols out.

Three modules, split along the line that matters:

* ``fields`` — the registry. The only place that decides what is screenable.
* ``compiler`` — the pure function from a validated tree to a SQLAlchemy
  boolean expression. No session, no I/O, tested directly.
* ``service`` — the query the compiler's expression goes into, plus preset CRUD.

The rule the whole design follows: **no user-supplied string ever reaches SQL**.
A filter names a field by key, the key is looked up in the registry, and an
unknown key has already failed Pydantic validation before the service is called.
There is no branch in which a field name is interpolated into a query.
"""

from __future__ import annotations
