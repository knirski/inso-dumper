"""Unit tests for the closed CliError family."""

from __future__ import annotations

from typing import assert_never

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult


def test_cli_error_kind_is_str_enum() -> None:
    assert CliErrorKind.CONFIG.value == "config"
    assert CliErrorKind.AUTH.value == "auth"
    assert CliErrorKind.SESSION_EXPIRED.value == "session_expired"
    assert CliErrorKind.HTTP.value == "http"
    assert CliErrorKind.PLATFORM_CHANGED.value == "platform_changed"
    assert CliErrorKind.INTERNAL.value == "internal"
    # StrEnum members ARE strs
    assert isinstance(CliErrorKind.CONFIG, str)


def test_cli_error_rejects_unknown_extra_fields() -> None:
    from typing import Any, cast

    with pytest.raises(TypeError):
        # The custom __init__ on CliError rejects unknown kwargs at runtime.
        # The cast to Any bypasses basedpyright's static signature check.
        cast(Any, CliError)(kind=CliErrorKind.AUTH, subject="x", extra="y")


def test_cli_error_accepts_known_fields() -> None:
    err = CliError(kind=CliErrorKind.AUTH, subject="missing_phpsessid")
    assert err.kind is CliErrorKind.AUTH
    assert err.subject == "missing_phpsessid"


def test_exit_code_dispatch_is_exhaustive() -> None:
    """A match over every CliErrorKind must compile; assert_never guards additions."""

    def _exit_for(kind: CliErrorKind) -> int:
        match kind:
            case CliErrorKind.CONFIG:
                return 2
            case CliErrorKind.AUTH:
                return 3
            case CliErrorKind.SESSION_EXPIRED:
                return 4
            case CliErrorKind.HTTP:
                return 5
            case CliErrorKind.PLATFORM_CHANGED:
                return 6
            case CliErrorKind.INTERNAL:
                return 99
            case _ as unreachable:  # type: ignore[unreachable]
                return assert_never(unreachable)

    assert _exit_for(CliErrorKind.CONFIG) == 2
    assert _exit_for(CliErrorKind.AUTH) == 3
    assert _exit_for(CliErrorKind.SESSION_EXPIRED) == 4
    assert _exit_for(CliErrorKind.HTTP) == 5
    assert _exit_for(CliErrorKind.PLATFORM_CHANGED) == 6
    assert _exit_for(CliErrorKind.INTERNAL) == 99


def test_cli_result_alias_resolves() -> None:
    # The alias should compose Ok/Err with CliError at the type level.
    ok: CliResult[int] = Ok(1)
    err: CliResult[int] = Err(CliError(kind=CliErrorKind.HTTP, subject="connect"))
    assert isinstance(ok, Ok)
    assert isinstance(err, Err)
