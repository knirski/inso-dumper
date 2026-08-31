"""Unit tests for the CLI's error presentation (_die)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from typer import Exit

from inso_dumper import cli
from inso_dumper.errors import CliError, CliErrorKind

LOG = logging.getLogger("test")


def _capture_print(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(cli._console_err, "print", lambda *a, **k: calls.append(a), raising=True)
    return calls


def test_die_prints_login_hint_for_session_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both session-expiry paths (missing local file, server-rejected
    saved session surfaced by the shells) must end with the re-login
    hint — it used to print only on the local path."""
    calls = _capture_print(monkeypatch)
    with pytest.raises(Exit) as excinfo:
        cli._die(CliError(kind=CliErrorKind.SESSION_EXPIRED, subject="children_list"), LOG)
    assert excinfo.value.exit_code == 4
    assert any(a == "Run `inso-dumper login` first." for a, *_ in calls)


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        (CliErrorKind.PLATFORM_CHANGED, 6),
        (CliErrorKind.HTTP, 5),
        (CliErrorKind.CONFIG, 2),
    ],
)
def test_die_prints_no_hint_for_other_kinds(
    monkeypatch: pytest.MonkeyPatch,
    kind: CliErrorKind,
    code: int,
) -> None:
    calls = _capture_print(monkeypatch)
    with pytest.raises(Exit) as excinfo:
        cli._die(CliError(kind=kind, subject="x"), LOG)
    assert excinfo.value.exit_code == code
    assert not any("login" in str(a) for a, *_ in calls)
