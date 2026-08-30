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


# --- sync command ------------------------------------------------------------

DASHBOARD_HTML = """
<el-menu id="menu-0" role="menu">
  <a href="https://app.inso.pl/panel/home/eea48660-3740-11ed-a611-06dd2728d782" id="i1">
    <div style="background-color: #FCCC34;"><span>FK</span></div>
    <span>Franek</span>
  </a>
</el-menu>
"""

SYNC_POST = {
    "id": "3d5b38b4-6c0b-4e79-8e55-375272c76779",
    "title": "Wycieczka",
    "content": "<p>hello</p>",
    "media": {
        "photos": [
            {
                "type": "photo",
                "name": "IMG_1.jpeg",
                "src": {
                    "thumb": "https://file.inso.pl/t/1/thumb.jpg",
                    "full": "https://file.inso.pl/t/1/full.jpeg",
                },
            }
        ],
        "videos": [],
        "attachments": [],
    },
    "createdAt": 1787811431,
    "createdAtText": "czwartek,  8:17",
    "visibleFor": ["Żółta"],
    "visibleForChildren": [],
    "actions": {"vote": "/v", "unvote": "/u"},
    "sticky": False,
    "archived": False,
    "userVoted": False,
    "author": "Lipińska Agnieszka",
    "likes": 0,
    "likesText": "0 polubień",
    "commentsAvailable": False,
    "comments": 0,
    "commentsText": "0 komentarzy",
    "isWorker": False,
    "isWorkerOnly": False,
    "displayed": True,
    "poll": None,
    "translations": [],
}


def _sync_script(second_run: bool) -> list[Any]:
    def page(items: list[Any]) -> tuple[int, bytes, list]:
        body = json.dumps({"items": items, "waitingToProcess": 0}).encode("utf-8")
        return (200, body, [])

    empty = page([])
    one_post = page([SYNC_POST])
    script: list[Any] = [(200, DASHBOARD_HTML.encode("utf-8"), []), one_post]
    if not second_run:
        script.append((200, b"jpeg-bytes", []))
    script.append(empty)
    return script


def _prepare_sync_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> Path:
    from inso_dumper.models.session import Session
    from tests.conftest import FakeHttpClient

    session_dir = tmp_path / "inso-dumper"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_store.save_session(
        session_dir / "session.json",
        Session(phpsessid="x", user_uuid=SPIKE_USER_UUID),
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("INSO_DUMPER_CONFIG", raising=False)

    client = FakeHttpClient(script)
    monkeypatch.setattr(cli_mod, "HttpxClient", lambda _cfg: client)
    return tmp_path / "dump"


def test_sync_missing_session_exits_4(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    result = cli_runner.invoke(app, ["sync", "franek"])
    assert result.exit_code == 4


def test_sync_unknown_category_exits_2(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    result = cli_runner.invoke(app, ["sync", "franek", "--category", "bogus"])
    assert result.exit_code == 2


def test_sync_unknown_slug_exits_6_and_lists_slugs(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    import rich.console

    buf = io.StringIO()
    monkeypatch.setattr(cli_mod, "_console_err", rich.console.Console(file=buf))
    dump = _prepare_sync_env(tmp_path, monkeypatch, _sync_script(second_run=True))
    result = cli_runner.invoke(app, ["sync", "nope", "--dump-root", str(dump)])
    combined = (result.stdout or "") + (result.stderr or "") + buf.getvalue()
    assert result.exit_code == 6
    assert "franek" in combined


def test_sync_happy_path_dumps_post_and_dedupes_media(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = _prepare_sync_env(tmp_path, monkeypatch, _sync_script(second_run=False))
    result = cli_runner.invoke(
        app, ["sync", "franek", "--category", "announcements", "--dump-root", str(dump)]
    )

    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    assert "Synced 1 announcements" in result.stdout
    assert "0 skipped" in result.stdout
    post_dir = dump / "franek" / "announcements" / "2026-08-27-wycieczka"
    assert (post_dir / "post.json").is_file()
    link = post_dir / "photos" / "1.jpeg"
    assert link.is_symlink()
    common = dump / "_common" / "photos"
    assert len(list(common.rglob("*.jpeg"))) == 1
    assert link.resolve().is_relative_to(common.resolve())


def test_sync_second_run_reports_skipped(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = _prepare_sync_env(tmp_path, monkeypatch, _sync_script(second_run=False))
    first = cli_runner.invoke(
        app, ["sync", "franek", "--category", "announcements", "--dump-root", str(dump)]
    )
    assert first.exit_code == 0, (first.stdout or "") + (first.stderr or "")

    # Rebuild the fake for the second run: children scrape + pages, no media.
    _prepare_sync_env(tmp_path, monkeypatch, _sync_script(second_run=True))
    second = cli_runner.invoke(
        app, ["sync", "franek", "--category", "announcements", "--dump-root", str(dump)]
    )

    assert second.exit_code == 0, (second.stdout or "") + (second.stderr or "")
    assert "1 skipped" in second.stdout
    assert "Synced 0 announcements" in second.stdout


# --- T10: category extension and per-type summary -------------------------------

CONV_ID = "5b8789d6-4b05-11ed-9234-06f545343a70"


def _conv_payload(last_update: int = 1787814351) -> dict[str, Any]:
    return {
        "id": CONV_ID,
        "lastUpdate": last_update,
        "recipient": {"name": "Zolta", "description": "W", "type": "teachers", "prefix": "G:"},
        "read": True,
        "excerpt": "e",
        "branch": "b",
        "participantsNames": [],
    }


def _msg_payload(msg_id: str, with_attachment: bool = False) -> dict[str, Any]:
    attachments: Any = []
    if with_attachment:
        attachments = {
            "media": [
                {
                    "name": "IMG_1.jpeg",
                    "url": {
                        "thumb": "https://file.inso.pl/t/1/thumb.jpg",
                        "full": "https://file.inso.pl/t/1/full.jpeg",
                    },
                    "isVideo": False,
                }
            ],
            "other": [],
        }
    return {
        "id": msg_id,
        "message": "hello",
        "sendDate": "czwartek,  9:05",
        "sendTimestamp": 1787814351,
        "sender": {"type": "worker", "name": "A", "initials": "A", "avatar": None},
        "incoming": True,
        "attachments": attachments,
        "main": False,
        "isRemoved": False,
        "canRemove": False,
    }


def _conv_page(convs: list[dict[str, Any]]) -> tuple[int, bytes, list]:
    body = json.dumps(
        {"categories": [], "category": "main", "conversations": convs, "templates": [], "unreadCount": 0}
    ).encode()
    return (200, body, [])


def _msg_page(msgs: list[dict[str, Any]]) -> tuple[int, bytes, list]:
    return (200, json.dumps(msgs).encode(), [])


def _messages_script() -> list[Any]:
    return [
        _conv_page([_conv_payload()]),
        _msg_page([_msg_payload("m1", with_attachment=True)]),
        _msg_page([]),
        (200, b"attachment-bytes", []),
        _conv_page([]),
    ]


def test_sync_messages_category_happy_path(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = _prepare_sync_env(tmp_path, monkeypatch, _messages_script())
    result = cli_runner.invoke(app, ["sync", "franek", "--category", "messages", "--dump-root", str(dump)])

    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    assert "1 conversations (1 new messages, 1 attachments)" in result.stdout
    conv_dir = dump / "messages" / "2026-08-27-zolta"
    assert (conv_dir / "conversation.json").is_file()
    assert (conv_dir / "messages.json").is_file()
    assert (conv_dir / "attachments" / "m1" / "1.jpeg").exists()
    from tests.conftest import FakeHttpClient  # noqa: F401


def test_sync_messages_second_run_reports_skipped(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = _prepare_sync_env(tmp_path, monkeypatch, _messages_script())
    first = cli_runner.invoke(app, ["sync", "franek", "--category", "messages", "--dump-root", str(dump)])
    assert first.exit_code == 0, (first.stdout or "") + (first.stderr or "")

    # same lastUpdate -> skipped; only list requests
    rerun = _prepare_sync_env(
        tmp_path,
        monkeypatch,
        [_conv_page([_conv_payload()]), _conv_page([])],
    )
    assert rerun is not None
    second = cli_runner.invoke(app, ["sync", "franek", "--category", "messages", "--dump-root", str(dump)])

    assert second.exit_code == 0, (second.stdout or "") + (second.stderr or "")
    assert "0 conversations (0 new messages, 0 attachments)" in second.stdout


def test_sync_both_category_rejected_exits_2(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_sync_env(tmp_path, monkeypatch, _messages_script())
    result = cli_runner.invoke(app, ["sync", "franek", "--category", "both"])
    assert result.exit_code == 2


def test_sync_default_runs_all_categories_documents_gated(
    cli_runner: typer.testing.CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No --category: posts (per child) + messages + documents. The
    documents gate is a loud skip, not a hard failure; its segment is
    absent from the summary."""
    galleries_empty = (200, json.dumps({"items": [], "waitingToProcess": 0}).encode(), [])
    script = [*_sync_script(second_run=False), galleries_empty, *_messages_script()]
    dump = _prepare_sync_env(tmp_path, monkeypatch, script)
    result = cli_runner.invoke(app, ["sync", "franek", "--dump-root", str(dump)])

    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    combined = (result.stdout or "") + (result.stderr or "")
    assert "1 announcements" in result.stdout
    assert "1 conversations (1 new messages, 1 attachments)" in result.stdout
    assert "documents for" not in result.stdout  # gate tripped: no segment
    assert "unverified" in combined  # loud skip warning
