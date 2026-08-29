"""Persisted session model.

The Inso platform issues one HttpOnly ``PHPSESSID`` cookie; the post-login
landing page URL exposes the user UUID. v1 persists just those two values.

Security: ``phpsessid`` is marked ``repr=False`` so accidental ``repr()``
or ``str()`` in logs, exceptions, or test failures does not leak the
cookie. The token-redacting filter at the logger boundary is the
primary defense; this is a belt-and-braces backstop.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Session(BaseModel):
    """The minimum state needed to authenticate future requests."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    phpsessid: str = Field(repr=False)
    user_uuid: str
    expires_at: datetime | None = None
