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


def test_cli_error_rejects_unknown_fields() -> None:
    """The dataclass-generated __init__ rejects unknown kwargs with TypeError."""
    from typing import Any, cast

    with pytest.raises(TypeError):
        # The cast silences basedpyright (which knows the field set) so
        # the runtime TypeError is exercised.
        cast(Any, CliError)(kind=CliErrorKind.AUTH, subject="x", bogus="y")


def test_cli_error_accepts_known_fields() -> None:
    err = CliError(kind=CliErrorKind.AUTH, subject="missing_phpsessid")
    assert err.kind is CliErrorKind.AUTH
    assert err.subject == "missing_phpsessid"


def test_cli_error_default_subject_is_empty() -> None:
    err = CliError(kind=CliErrorKind.CONFIG)
    assert err.subject == ""


def test_cli_error_is_frozen() -> None:
    err = CliError(kind=CliErrorKind.HTTP, subject="connect")
    # Indirect setattr via a generic object handle so the static
    # checker doesn't flag the line; the frozen dataclass raises
    # at runtime.
    target: object = err
    with pytest.raises((AttributeError, Exception)):
        setattr(target, "subject", "forged")  # noqa: B010


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
    ok: CliResult[int] = Ok(1)
    err: CliResult[int] = Err(CliError(kind=CliErrorKind.HTTP, subject="connect"))
    assert isinstance(ok, Ok)
    assert isinstance(err, Err)
