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
import dataclasses
import json
import os
import sys
from collections.abc import Awaitable, Callable
from enum import StrEnum
from logging import Logger
from pathlib import Path
from typing import Any, NoReturn, assert_never

import typer
from rich.console import Console
from rich.table import Table

from inso_dumper._result import Err, Ok
from inso_dumper.config import load_config
from inso_dumper.dump.sync import SyncSummary
from inso_dumper.dump.sync import run as sync_run
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
from inso_dumper.models.timeline import Category
from inso_dumper.paths import session_file
from inso_dumper.session.store import save_session

app = typer.Typer(
    name="inso-dumper",
    help="Local backup tool for the Inso platform (app.inso.pl).",
    no_args_is_help=True,
    add_completion=False,
)

_console_err = Console(file=sys.stderr)
_console_out = Console()


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version

        _console_out.print(version("inso-dumper"))
        raise typer.Exit(0)


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    """Global options: --version."""


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


def _format_error(err: CliError) -> str:
    return f"error[{err.kind.value}]: {err.subject}" if err.subject else f"error[{err.kind.value}]"


def _die(err: CliError, log: Logger) -> NoReturn:
    log.error("%s", _format_error(err))
    _console_err.print(_format_error(err), style="red")
    raise typer.Exit(exit_code_for(err))


def _dispatch(
    coro_factory: Callable[[], Awaitable[CliResult[Any]]],
    log: Logger,
) -> CliResult[Any]:
    """Run a coroutine in a single asyncio.run with the last-resort catch.

    ``coro_factory`` builds the coroutine so the HttpxClient is constructed
    inside the event loop (where its async __aenter__ runs).
    """
    try:
        return asyncio.run(coro_factory())
    except Exception as exc:  # last-resort safety net per AGENTS.md
        log.exception("command: unexpected exception")
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject=type(exc).__name__))


@app.command()
def login(
    config_path: Path | None = typer.Option(
        None, "--config", help="Alternate TOML config file path."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG-level logging."),
) -> NoReturn:
    """Authenticate against the Inso platform and persist the session."""
    setup_logging(verbose=verbose or is_verbose())
    log = get_logger("cli")

    email = os.environ.get("INSO_EMAIL", "")
    password = os.environ.get("INSO_PASSWORD", "")
    if not email or not password:
        _die(
            CliError(kind=CliErrorKind.CONFIG, subject="missing_credentials"),
            log,
        )

    async def _do_login() -> CliResult[Any]:
        cfg_result = load_config(config_path)
        if isinstance(cfg_result, Err):
            return cfg_result
        cfg = cfg_result.value
        async with HttpxClient(cfg) as client:
            return await login_shell(client, cfg, email, password)

    result = _dispatch(_do_login, log)
    if isinstance(result, Err):
        _die(result.error, log)
    session = result.value

    save = save_session(session_file(), session)
    if isinstance(save, Err):
        _die(save.error, log)

    log.info("login ok user_uuid=%s", session.user_uuid)
    _console_out.print("OK")
    raise typer.Exit(0)


@app.command()
def children(
    config_path: Path | None = typer.Option(
        None, "--config", help="Alternate TOML config file path."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a rich table."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG-level logging."),
) -> NoReturn:
    """List children on the authenticated account."""
    setup_logging(verbose=verbose or is_verbose())
    log = get_logger("cli")

    session_result = ensure_session_loaded(session_file())
    if isinstance(session_result, Err):
        if session_result.error.kind is CliErrorKind.SESSION_EXPIRED:
            _console_err.print(
                "No usable saved session. Run `inso-dumper login` first.",
                style="yellow",
            )
        _die(session_result.error, log)
    session = session_result.value

    async def _do_list() -> CliResult[Any]:
        cfg_result = load_config(config_path)
        if isinstance(cfg_result, Err):
            return cfg_result
        cfg = cfg_result.value
        async with HttpxClient(cfg) as client:
            return await list_children_shell(client, cfg, session)

    result = _dispatch(_do_list, log)
    if isinstance(result, Err):
        _die(result.error, log)
    kids = result.value

    if as_json:
        _console_out.print(json.dumps([_child_to_dict(c) for c in kids], indent=2))
    else:
        if not kids:
            _console_out.print("No children on this account.")
        else:
            table = Table(title="Children")
            table.add_column("Slug", style="cyan")
            table.add_column("Name", style="white")
            table.add_column("Group", style="dim")
            for c in kids:
                table.add_row(c.slug, c.display_name, c.group or "-")
            _console_out.print(table)
    raise typer.Exit(0)


def _child_to_dict(c: Any) -> dict[str, Any]:
    """Serialize a Child for --json. Use asdict for the fields, then add
    the computed properties so the JSON output stays in sync with the
    dataclass automatically.
    """
    return {**dataclasses.asdict(c), "slug": c.slug, "display_name": c.display_name}


class SyncCategory(StrEnum):
    """The ``--category`` choice set for ``sync``."""

    ANNOUNCEMENTS = "announcements"
    GALLERIES = "galleries"
    BOTH = "both"


def _categories_for(choice: SyncCategory) -> list[Category]:
    match choice:
        case SyncCategory.ANNOUNCEMENTS:
            return [Category.ANNOUNCEMENTS]
        case SyncCategory.GALLERIES:
            return [Category.GALLERIES]
        case SyncCategory.BOTH:
            return [Category.ANNOUNCEMENTS, Category.GALLERIES]
        case _ as unreachable:
            return assert_never(unreachable)


def _ensure_dump_root(root: Path) -> CliResult[Path]:
    """Create the dump root (0o700); a bad path is a user/config error."""
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="dump_root"))
    return Ok(root)


@app.command()
def sync(
    child_slug: str = typer.Argument(..., help="Child slug as shown by `inso-dumper children`."),
    category: SyncCategory = typer.Option(
        SyncCategory.BOTH,
        "--category",
        help="Which categories to sync: announcements, galleries, or both.",
    ),
    dump_root: Path = typer.Option(
        Path("dump"), "--dump-root", help="Dump output directory (created if missing)."
    ),
    force: list[str] = typer.Option(
        None,
        "--force",
        help="Post slug to re-download even if already dumped; repeatable.",
    ),
    config_path: Path | None = typer.Option(
        None, "--config", help="Alternate TOML config file path."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG-level logging."),
) -> NoReturn:
    """Dump announcements and galleries for one child, deduplicated."""
    setup_logging(verbose=verbose or is_verbose())
    log = get_logger("cli")

    session_result = ensure_session_loaded(session_file())
    if isinstance(session_result, Err):
        if session_result.error.kind is CliErrorKind.SESSION_EXPIRED:
            _console_err.print(
                "No usable saved session. Run `inso-dumper login` first.",
                style="yellow",
            )
        _die(session_result.error, log)
    session = session_result.value

    root = dump_root.expanduser().resolve()
    root_result = _ensure_dump_root(root)
    if isinstance(root_result, Err):
        _die(root_result.error, log)

    available: list[str] = []

    async def _do_sync() -> CliResult[Any]:
        cfg_result = load_config(config_path)
        if isinstance(cfg_result, Err):
            return cfg_result
        cfg = cfg_result.value
        totals = SyncSummary(0, 0, 0, 0, 0, 0.0)
        async with HttpxClient(cfg) as client:
            kids_result = await list_children_shell(client, cfg, session)
            child: Any = None
            match kids_result:
                case Err(error):
                    return Err(error)
                case Ok(kids):
                    available.extend(k.slug for k in kids)
                    child = next((k for k in kids if k.slug == child_slug), None)
            if child is None:
                return Err(
                    CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="unknown_child_slug")
                )
            for cat in _categories_for(category):
                run_result = await sync_run(
                    client=client,
                    session=session,
                    child=child,
                    category=cat,
                    dump_root=root,
                    force=set(force) if force else set(),
                    log=log,
                )
                match run_result:
                    case Err(error):
                        return Err(error)
                    case Ok(s):
                        totals = SyncSummary(
                            totals.posts_new + s.posts_new,
                            totals.posts_skipped + s.posts_skipped,
                            totals.photos + s.photos,
                            totals.videos + s.videos,
                            totals.attachments + s.attachments,
                            totals.seconds + s.seconds,
                        )
        return Ok(totals)

    result = _dispatch(_do_sync, log)
    if isinstance(result, Err):
        if result.error.subject == "unknown_child_slug" and available:
            _console_err.print(f"Available slugs: {', '.join(available)}", style="yellow")
        _die(result.error, log)
    summary = result.value

    log.info(
        "sync done child=%s new=%d skipped=%d", child_slug, summary.posts_new, summary.posts_skipped
    )
    _console_out.print(
        f"Synced {summary.posts_new} posts ({summary.posts_skipped} skipped, "
        f"{summary.photos} photos, {summary.videos} videos, "
        f"{summary.attachments} attachments) for {child_slug} in {summary.seconds:.1f}s."
    )
    raise typer.Exit(0)
