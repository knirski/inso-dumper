"""CLI entry point: typer app, command wiring, exit-code dispatch.

The CLI is the only place that:
  - reads env and config,
  - builds an ``HttpClient``,
  - maps a closed ``CliError`` family to documented exit codes,
  - renders output.

The single outer ``try/except Exception`` in each command is the
last-resort safety net per AGENTS.md. Every other function returns
``CliResult[T]``.

Note: we do NOT use ``from __future__ import annotations`` here so
that typer's runtime annotation introspection sees real types.
"""

# ruff: noqa: B008  # typer.Option as default is the documented typer idiom.

import asyncio
import json
import os
import sys
from logging import Logger
from pathlib import Path
from typing import Any, NoReturn, assert_never

import typer
from rich.console import Console
from rich.table import Table

from inso_dumper._result import Err, Ok
from inso_dumper.config import load_config
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.children_shell import list_children as list_children_shell
from inso_dumper.http.client import HttpxClient
from inso_dumper.http.login import login as login_shell
from inso_dumper.http.session_expiry import ensure_session_loaded
from inso_dumper.logging_setup import (
    get_logger,
    is_verbose,
    setup_logging,
)
from inso_dumper.paths import session_file
from inso_dumper.session.store import save_session

app = typer.Typer(
    name="inso-dumper",
    help="Local backup tool for the Inso platform (app.inso.pl).",
    no_args_is_help=True,
    add_completion=False,
)

console_err = Console(file=sys.stderr)
console_out = Console()


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version

        console_out.print(version("inso-dumper"))
        raise typer.Exit(0)


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    """Global options: --version."""
    return None


def exit_code_for(err: CliError) -> int:
    """Map a CliError to its documented exit code."""
    match err.kind:
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


def _read_credentials() -> CliResult[tuple[str, str]]:
    email = os.environ.get("INSO_EMAIL", "")
    password = os.environ.get("INSO_PASSWORD", "")
    if not email or not password:
        return Err(
            CliError(
                kind=CliErrorKind.CONFIG,
                subject="missing_credentials",
            )
        )
    return Ok((email, password))


def _format_error(err: CliError) -> str:
    return f"error[{err.kind.value}]: {err.subject}" if err.subject else f"error[{err.kind.value}]"


@app.command()
def login(
    config_path: Path | None = typer.Option(
        None, "--config", help="Alternate TOML config file path."
    ),
) -> NoReturn:
    """Authenticate against the Inso platform and persist the session."""
    setup_logging(verbose=is_verbose())
    log = get_logger("cli")

    cfg_result = load_config(config_path)
    if isinstance(cfg_result, Err):
        _die(cfg_result.error, log)
    cfg = cfg_result.value

    creds = _read_credentials()
    if isinstance(creds, Err):
        _die(creds.error, log)
    email, password = creds.value

    client = HttpxClient(cfg)
    try:
        session_result = asyncio.run(login_shell(client, cfg, email, password))
    except Exception as exc:  # last-resort safety net
        log.exception("login: unexpected exception")
        _die(
            CliError(kind=CliErrorKind.INTERNAL, subject=type(exc).__name__),
            log,
        )
    finally:
        asyncio.run(client.aclose())

    if isinstance(session_result, Err):
        _die(session_result.error, log)
    session = session_result.value

    save = save_session(session_file(), session)
    if isinstance(save, Err):
        _die(save.error, log)

    log.info("login ok user_uuid=%s", session.user_uuid)
    console_out.print("OK")
    raise typer.Exit(0)


@app.command()
def children(
    config_path: Path | None = typer.Option(
        None, "--config", help="Alternate TOML config file path."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a rich table."),
) -> NoReturn:
    """List children on the authenticated account."""
    setup_logging(verbose=is_verbose())
    log = get_logger("cli")

    cfg_result = load_config(config_path)
    if isinstance(cfg_result, Err):
        _die(cfg_result.error, log)
    cfg = cfg_result.value

    session_result = ensure_session_loaded(session_file())
    if isinstance(session_result, Err):
        if session_result.error.kind is CliErrorKind.SESSION_EXPIRED:
            console_out.print(
                "No saved session. Run `inso-dumper login` first.",
                style="yellow",
            )
        _die(session_result.error, log)
    session = session_result.value

    client = HttpxClient(cfg)
    try:
        children_result = asyncio.run(list_children_shell(client, cfg, session))
    except Exception as exc:  # last-resort safety net
        log.exception("children: unexpected exception")
        _die(
            CliError(kind=CliErrorKind.INTERNAL, subject=type(exc).__name__),
            log,
        )
    finally:
        asyncio.run(client.aclose())

    if isinstance(children_result, Err):
        _die(children_result.error, log)
    kids = children_result.value

    if as_json:
        console_out.print(json.dumps([_child_to_dict(c) for c in kids], indent=2))
    else:
        if not kids:
            console_out.print("No children on this account.")
        else:
            table = Table(title="Children")
            table.add_column("Slug", style="cyan")
            table.add_column("Name", style="white")
            table.add_column("Group", style="dim")
            for c in kids:
                table.add_row(c.slug, c.display_name, c.group or "-")
            console_out.print(table)
    raise typer.Exit(0)


def _child_to_dict(c: Any) -> dict[str, Any]:
    return {
        "child_id": c.child_id,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "group": c.group,
        "avatar_color": c.avatar_color,
        "initials": c.initials,
        "slug": c.slug,
        "display_name": c.display_name,
    }


def _die(err: CliError, log: Logger) -> NoReturn:
    log.error("%s", _format_error(err))
    console_err.print(_format_error(err), style="red")
    raise typer.Exit(exit_code_for(err))
