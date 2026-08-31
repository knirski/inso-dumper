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
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from logging import Logger
from pathlib import Path
from typing import Any, NoReturn, assert_never

import typer
from rich.console import Console
from rich.table import Table

from inso_dumper._result import Err, Ok
from inso_dumper.config import load_config
from inso_dumper.dump.audit import materialize_dump, verify_dump
from inso_dumper.dump.indexing import DumpIndex, write_index
from inso_dumper.dump.sync import (
    SyncSummary,
    run_documents,
    run_messages,
    run_settlements,
)
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
from inso_dumper.models.children import Child
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
    if err.kind is CliErrorKind.SESSION_EXPIRED:
        # Covers both the local session-file path and a saved session the
        # server rejected mid-run (the shell surfaces that as the same kind).
        _console_err.print("Run `inso-dumper login` first.", style="yellow")
    raise typer.Exit(exit_code_for(err))


def _dispatch[ValueT](
    coro_factory: Callable[[], Awaitable[CliResult[ValueT]]],
    log: Logger,
) -> CliResult[ValueT]:
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
    """The ``--category`` choice set for ``sync`` (``all`` replaces the
    pre-release ``both``)."""

    ANNOUNCEMENTS = "announcements"
    GALLERIES = "galleries"
    MESSAGES = "messages"
    DOCUMENTS = "documents"
    SETTLEMENTS = "settlements"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class _CategoryPlan:
    """What one ``--category`` choice expands to. Posts categories and
    settlements are per child; messages and documents are account-level
    and run exactly once per invocation regardless of the child
    argument."""

    posts: list[Category]
    messages: bool
    documents: bool
    settlements: bool = False


def _plan_for(choice: SyncCategory) -> _CategoryPlan:
    match choice:
        case SyncCategory.ANNOUNCEMENTS:
            return _CategoryPlan([Category.ANNOUNCEMENTS], messages=False, documents=False)
        case SyncCategory.GALLERIES:
            return _CategoryPlan([Category.GALLERIES], messages=False, documents=False)
        case SyncCategory.MESSAGES:
            return _CategoryPlan([], messages=True, documents=False)
        case SyncCategory.DOCUMENTS:
            return _CategoryPlan([], messages=False, documents=True)
        case SyncCategory.SETTLEMENTS:
            return _CategoryPlan([], messages=False, documents=False, settlements=True)
        case SyncCategory.ALL:
            return _CategoryPlan(
                [Category.ANNOUNCEMENTS, Category.GALLERIES],
                messages=True,
                documents=True,
                settlements=True,
            )
        case _ as unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class _SyncTotals:
    """Per-segment summaries; ``None`` when the category did not run."""

    announcements: SyncSummary | None = None
    galleries: SyncSummary | None = None
    messages: SyncSummary | None = None
    documents: SyncSummary | None = None
    settlements: SyncSummary | None = None

    @property
    def seconds(self) -> float:
        parts = (
            self.announcements,
            self.galleries,
            self.messages,
            self.documents,
            self.settlements,
        )
        return sum(s.seconds for s in parts if s is not None)

    def with_segment(self, name: str, summary: SyncSummary) -> _SyncTotals:
        """A copy with one segment set, the others preserved."""
        data = {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
        data[name] = summary
        return _SyncTotals(**data)


def _summary_line(totals: _SyncTotals, child_slug: str) -> str:
    """The design.md summary format: one segment per category that ran;
    the ``for <child>`` tail covers only the per-child posts segments."""
    segments: list[str] = []
    if totals.announcements is not None:
        s = totals.announcements
        segments.append(
            f"{s.posts_new} announcements ({s.posts_skipped} skipped, "
            f"{s.photos} photos, {s.videos} videos, {s.attachments} attachments)"
        )
    if totals.galleries is not None:
        segments.append(f"{totals.galleries.posts_new} galleries")
    if totals.messages is not None:
        s = totals.messages
        segments.append(
            f"{s.conversations} conversations ({s.messages} new messages, "
            f"{s.message_attachments} attachments)"
        )
    if totals.documents is not None:
        segments.append(f"{totals.documents.documents} documents")
    if totals.settlements is not None:
        s = totals.settlements
        segments.append(
            f"{s.settlements} settlements ({s.settlements_skipped} skipped, "
            f"{s.settlements_invoices} invoices)"
        )
    who = (
        child_slug
        if (totals.announcements or totals.galleries or totals.settlements)
        else "the account"
    )
    return f"Synced {', '.join(segments)} for {who} in {totals.seconds:.1f}s."


def _ensure_dump_root(root: Path) -> CliResult[Path]:
    """Create the dump root (0o700, re-tightened if it pre-exists); a bad
    path is a user/config error."""
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
    except OSError:
        return Err(CliError(kind=CliErrorKind.CONFIG, subject="dump_root"))
    return Ok(root)


@app.command()
def sync(
    child_slug: str = typer.Argument(..., help="Child slug as shown by `inso-dumper children`."),
    category: SyncCategory = typer.Option(
        SyncCategory.ALL,
        "--category",
        help=(
            "Which categories to sync: announcements, galleries, messages, "
            "documents, settlements, or all."
        ),
    ),
    dump_root: Path = typer.Option(
        Path("dump"), "--dump-root", help="Dump output directory (created if missing)."
    ),
    force: list[str] = typer.Option(
        None,
        "--force",
        help=(
            "Re-dump even if recorded; repeatable. Posts: slug. Messages: conversation id "
            "or dir name (refetches history from 0). Documents: re-walk. "
            "Settlements: YYYY-MM month key (re-downloads that month's invoice)."
        ),
    ),
    config_path: Path | None = typer.Option(
        None, "--config", help="Alternate TOML config file path."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG-level logging."),
) -> NoReturn:
    """Dump announcements, galleries, messages, and documents (deduplicated,
    incremental)."""
    setup_logging(verbose=verbose or is_verbose())
    log = get_logger("cli")

    session_result = ensure_session_loaded(session_file())
    if isinstance(session_result, Err):
        _die(session_result.error, log)
    session = session_result.value

    root = dump_root.expanduser().resolve()
    root_result = _ensure_dump_root(root)
    if isinstance(root_result, Err):
        _die(root_result.error, log)

    available: list[str] = []

    async def _do_sync() -> CliResult[_SyncTotals]:
        cfg_result = load_config(config_path)
        if isinstance(cfg_result, Err):
            return cfg_result
        cfg = cfg_result.value
        plan = _plan_for(category)
        totals = _SyncTotals()
        child: Child | None = None
        async with HttpxClient(cfg) as client:
            # Settlements are per child too, so the child must resolve
            # whenever either per-child kind is planned.
            if plan.posts or plan.settlements:
                kids_result = await list_children_shell(client, cfg, session)
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
            if plan.posts:
                assert child is not None  # per-child posts ⇒ resolved above
                for cat in plan.posts:
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
                            segment = (
                                "announcements" if cat is Category.ANNOUNCEMENTS else "galleries"
                            )
                            totals = totals.with_segment(segment, s)
            if plan.messages:
                run_result = await run_messages(
                    client=client,
                    session=session,
                    dump_root=root,
                    force=set(force) if force else set(),
                    log=log,
                )
                match run_result:
                    case Err(error):
                        return Err(error)
                    case Ok(s):
                        totals = totals.with_segment("messages", s)
            if plan.documents:
                run_result = await run_documents(
                    client=client, session=session, dump_root=root, log=log
                )
                match run_result:
                    case Err(CliError(kind=CliErrorKind.CONFIG, subject="documents_unverified")):
                        # Gated spike task: drive download shape unverified.
                        # A loud skip, not a hard failure — the account's
                        # other categories must not be blocked by it.
                        log.warning("documents category unavailable: %s", run_result.error.subject)
                        _console_err.print(
                            "Documents skipped: drive download shape not yet verified.",
                            style="yellow",
                        )
                    case Err(error):
                        return Err(error)
                    case Ok(s):
                        totals = totals.with_segment("documents", s)
            if plan.settlements:
                # Per child; ``child`` resolved above when either
                # per-child kind is planned. Runs last so the
                # account-level walk order is unchanged.
                assert child is not None  # plan.settlements ⇒ resolved above
                run_result = await run_settlements(
                    client=client,
                    config=cfg,
                    session=session,
                    child=child,
                    dump_root=root,
                    force=set(force) if force else set(),
                    log=log,
                )
                match run_result:
                    case Err(error):
                        return Err(error)
                    case Ok(s):
                        totals = totals.with_segment("settlements", s)
        return Ok(totals)

    result = _dispatch(_do_sync, log)
    if isinstance(result, Err):
        if result.error.subject == "unknown_child_slug" and available:
            _console_err.print(f"Available slugs: {', '.join(available)}", style="yellow")
        _die(result.error, log)
    totals = result.value

    log.info(
        "sync done child=%s announcements=%d skipped=%d conversations=%d",
        child_slug,
        totals.announcements.posts_new if totals.announcements else 0,
        totals.announcements.posts_skipped if totals.announcements else 0,
        totals.messages.conversations if totals.messages else 0,
    )
    _console_out.print(_summary_line(totals, child_slug))
    raise typer.Exit(0)


@app.command()
def index(
    dump_root: Path = typer.Option(
        Path("dump"), "--dump-root", help="Dump directory to index (created if missing)."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG-level logging."),
) -> NoReturn:
    """(Re)build _index.json and the top-level/event index.html pages.

    Fully offline: scans the dump tree, needs no session or network.
    Safe to re-run any time; skips malformed entries with a warning.
    """
    setup_logging(verbose=verbose or is_verbose())
    log = get_logger("cli")

    root = dump_root.expanduser().resolve()
    root_result = _ensure_dump_root(root)
    if isinstance(root_result, Err):
        _die(root_result.error, log)

    async def _do_index() -> CliResult[DumpIndex]:
        return write_index(root, log=log)

    start = time.monotonic()
    result = _dispatch(_do_index, log)
    if isinstance(result, Err):
        _die(result.error, log)
    idx = result.value
    events = sum(len(c.events) for c in idx.children)
    log.info(
        "index done children=%d events=%d conversations=%d settlements=%d",
        len(idx.children),
        events,
        len(idx.conversations),
        len(idx.settlements),
    )
    _console_out.print(
        f"Indexed {events} events across {len(idx.children)} children "
        f"({len(idx.conversations)} conversations, {len(idx.settlements)} settlement "
        f"children) in {time.monotonic() - start:.1f}s."
    )
    raise typer.Exit(0)


def _offline_dump_command[ReportT](
    dump_root: Path,
    verbose: bool,
    run: Callable[[Path, Logger], CliResult[ReportT]],
) -> tuple[ReportT, float]:
    """Shared body for the offline dump commands (index, verify,
    materialize): no session, no network, one timed dump-tree pass.
    Returns ``(report, elapsed_seconds)``."""
    setup_logging(verbose=verbose or is_verbose())
    log = get_logger("cli")

    root = dump_root.expanduser().resolve()
    root_result = _ensure_dump_root(root)
    if isinstance(root_result, Err):
        _die(root_result.error, log)

    async def _do() -> CliResult[ReportT]:
        return run(root, log)

    start = time.monotonic()
    result = _dispatch(_do, log)
    if isinstance(result, Err):
        _die(result.error, log)
    return result.value, time.monotonic() - start


@app.command()
def verify(
    dump_root: Path = typer.Option(Path("dump"), "--dump-root", help="Dump directory to audit."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG-level logging."),
) -> NoReturn:
    """Recompute blob checksums and report missing/corrupt files.

    Exits 0 when the dump is clean, 1 when findings exist (corrupt
    blobs, dangling links, unexpected store files)."""
    log = get_logger("cli")
    report, seconds = _offline_dump_command(dump_root, verbose, verify_dump)
    log.info(
        "verify done blobs=%d corrupt=%d links=%d dangling=%d",
        report.blobs,
        len(report.corrupt),
        report.links,
        len(report.dangling),
    )
    _console_out.print(
        f"Verified {report.blobs} blobs ({len(report.corrupt)} corrupt), "
        f"{report.links} links ({len(report.dangling)} dangling) in {seconds:.1f}s."
    )
    if report.corrupt or report.unexpected or report.dangling:
        _console_err.print(
            "Integrity findings: "
            f"{len(report.corrupt)} corrupt, {len(report.unexpected)} unexpected, "
            f"{len(report.dangling)} dangling. Run with -v for the full list.",
            style="red",
        )
        raise typer.Exit(1)
    raise typer.Exit(0)


@app.command()
def materialize(
    dump_root: Path = typer.Option(
        Path("dump"), "--dump-root", help="Dump directory to materialize."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG-level logging."),
) -> NoReturn:
    """Replace symlinks with real copies (makes the tree self-contained).

    ``_common/`` is kept: future syncs still dedup against it. Dangling
    links are skipped with a warning; safe to re-run."""
    log = get_logger("cli")
    report, seconds = _offline_dump_command(dump_root, verbose, materialize_dump)
    log.info("materialize done copied=%d dangling=%d", report.copied, report.skipped_dangling)
    _console_out.print(
        f"Materialized {report.copied} links ({report.skipped_dangling} dangling "
        f"skipped) in {seconds:.1f}s."
    )
    raise typer.Exit(0)
