"""User-facing representations of an account."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserResponse(BaseModel):
    """A user as the API returns it.

    Deliberately narrow: ``hashed_password`` has no business leaving the
    process, so it is not a field here rather than being excluded later.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str | None
    is_active: bool
    created_at: datetime
