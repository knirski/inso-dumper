"""Integration tests for the CLI surface using ``typer.testing.CliRunner``.

The runner drives the typer app in-process (no subprocess). Subprocess-
based smoke tests would belong in a separate file added when needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer.testing

from inso_dumper import cli as cli_mod
from inso_dumper._result import Ok
from inso_dumper.cli import app, exit_code_for
from inso_dumper.config import Config
from inso_dumper.errors import CliError, CliErrorKind
from inso_dumper.models.children import Child
from inso_dumper.session import store as session_store
from tests.conftest import SPIKE_USER_UUID, make_session


@pytest.fixture
def cli_runner() -> typer.testing.CliRunner:
    return typer.testing.CliRunner()


def test_help_exits_zero(cli_runner: typer.testing.CliRunner) -> None:
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "login" in result.stdout
    assert "children" in result.stdout


def test_version_prints_package_version(cli_runner: typer.testing.CliRunner) -> None:
    result = cli_runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1" in result.stdout


def test_verbose_flag_accepted(cli_runner: typer.testing.CliRunner) -> None:
    """The -v / --verbose flag documented in design.md is wired through."""
    # The flag exists on each command and exits cleanly (children with
    # no session is exit 4).
    result = cli_runner.invoke(app, ["children", "-v"])
    assert result.exit_code in (0, 4)


def test_login_missing_env_exits_2(
    cli_runner: typer.testing.CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INSO_EMAIL", raising=False)
    monkeypatch.delenv("INSO_PASSWORD", raising=False)
    result = cli_runner.invoke(app, ["login"])
    assert result.exit_code == 2


def test_children_missing_session_exits_4(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("INSO_DUMPER_CONFIG", raising=False)
    result = cli_runner.invoke(app, ["children"])
    assert result.exit_code == 4
    # CliRunner's capture shape varies by typer version; assert at the
    # most-stable layer (exit code + the error-kind string).
    combined = (result.stdout or "") + (result.stderr or "")
    assert "session" in combined.lower()


def test_exit_code_for_dispatch() -> None:
    """Every CliErrorKind maps to a stable, documented exit code."""
    assert exit_code_for(CliError(kind=CliErrorKind.CONFIG)) == 2
    assert exit_code_for(CliError(kind=CliErrorKind.AUTH)) == 3
    assert exit_code_for(CliError(kind=CliErrorKind.SESSION_EXPIRED)) == 4
    assert exit_code_for(CliError(kind=CliErrorKind.HTTP)) == 5
    assert exit_code_for(CliError(kind=CliErrorKind.PLATFORM_CHANGED)) == 6
    assert exit_code_for(CliError(kind=CliErrorKind.INTERNAL)) == 99


def test_children_with_fake_client_renders_json(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end with a stubbed HttpClient and a valid session."""
    session = make_session()
    session_dir = tmp_path / "inso-dumper"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / "session.json"
    save_result = session_store.save_session(session_path, session)
    assert isinstance(save_result, Ok)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("INSO_DUMPER_CONFIG", raising=False)

    async def fake_list_children(client: Any, config: Config, session: Any) -> Ok[list[Child]]:
        return Ok(
            [
                Child(
                    child_id="aaa",
                    first_name="Anna",
                    last_name="Kowalska",
                    group="Biedronki",
                    avatar_color="#FCCC34",
                    initials="AK",
                ),
                Child(
                    child_id="bbb",
                    first_name="Bartek",
                    last_name="Nowak",
                    group=None,
                    avatar_color="#A9CC63",
                    initials="BN",
                ),
            ]
        )

    monkeypatch.setattr(cli_mod, "list_children_shell", fake_list_children)

    result = cli_runner.invoke(app, ["children", "--json"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["first_name"] == "Anna"
    assert payload[0]["slug"] == "anna-k"
    assert payload[1]["first_name"] == "Bartek"


def test_internal_exit_code_on_unexpected_exception(
    cli_runner: typer.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A RuntimeError that escapes the result boundary is caught and returns 99."""
    from inso_dumper.models.session import Session

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated programmer error")

    monkeypatch.setattr(cli_mod, "list_children_shell", boom)
    monkeypatch.setattr(
        cli_mod,
        "ensure_session_loaded",
        lambda path: Ok(Session(phpsessid="x", user_uuid=SPIKE_USER_UUID)),
    )

    session_dir = tmp_path / "inso-dumper"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_store.save_session(
        session_dir / "session.json",
        Session(phpsessid="x", user_uuid=SPIKE_USER_UUID),
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("INSO_DUMPER_CONFIG", raising=False)
    result = cli_runner.invoke(app, ["children"])
    assert result.exit_code == 99
