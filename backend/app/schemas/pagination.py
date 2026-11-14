"""Cursor pagination, for lists that grow without bound.

Keyset, not offset. A notification list gets new rows at the top while someone
is paging through it. With an offset, each arrival shifts every later page by
one, so a row is skipped or shown twice. A cursor names the last row seen, as
``(timestamp, id)``, and the next page is "older than that". UUIDv7 ids are
time-ordered, so the id is a stable tiebreak between rows that share one
transaction's ``now()``.

The cursor is opaque to clients: base64 of the pair, so nobody builds one by
hand and the encoding can change without an API change.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.exceptions import ValidationError

__all__ = ["DEFAULT_PAGE_SIZE", "MAX_PAGE_SIZE", "Cursor", "decode_cursor", "encode_cursor"]

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class Cursor(BaseModel):
    at: datetime
    id: uuid.UUID


def encode_cursor(at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token: str | None) -> Cursor | None:
    """Parse a cursor from a query string, or raise a 422 naming it."""
    if token is None:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        at, row_id = base64.urlsafe_b64decode(padded.encode()).decode().split("|", 1)
        return Cursor(at=datetime.fromisoformat(at), id=uuid.UUID(row_id))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationError("The 'cursor' parameter is not a valid cursor.") from exc


PageSize = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
