"""Persisted session model.

The Inso platform issues one HttpOnly ``PHPSESSID`` cookie; the post-login
landing page URL exposes the user UUID. v1 persists just those two values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Session(BaseModel):
    """The minimum state needed to authenticate future requests."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    phpsessid: str
    user_uuid: str
    expires_at: datetime | None = None
