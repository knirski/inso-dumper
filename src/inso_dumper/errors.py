"""Closed CLI error family and exit-code mapping.

The CLI is the single dispatcher that maps each ``CliErrorKind`` to the
documented exit code. New variants must be added here, in the dispatcher
in ``cli.py``, and in tests for the dispatch table.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from inso_dumper._result import Err, Ok, Result


class CliErrorKind(StrEnum):
    """Stable, documented CLI error categories. The string value is the
    stable identifier; the integer exit code is derived in ``cli.py``."""

    CONFIG = "config"  # exit 2 — bad/missing config or env
    AUTH = "auth"  # exit 3 — login rejected
    SESSION_EXPIRED = "session_expired"  # exit 4 — saved PHPSESSID no longer works
    HTTP = "http"  # exit 5 — transport-level failure
    PLATFORM_CHANGED = "platform_changed"  # exit 6 — payload shape drift
    INTERNAL = "internal"  # exit 99 — unclassified (should not happen)


@dataclass(frozen=True, slots=True)
class CliError:
    """A typed CLI error. ``subject`` is a short redacted identifier
    (e.g. ``"missing_phpsessid"``) safe to log; never include raw tokens.

    The dataclass-generated ``__init__`` already rejects unknown kwargs
    with ``TypeError``; no custom override is needed.
    """

    kind: CliErrorKind
    subject: str = ""


type CliResult[T] = Result[T, CliError]

__all__ = ["CliError", "CliErrorKind", "CliResult", "Err", "Ok", "Result"]
