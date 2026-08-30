"""Unit tests for the list_children shell."""

from __future__ import annotations

import asyncio

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliError, CliErrorKind
from inso_dumper.http.children_shell import list_children
from inso_dumper.http.client import Response
from tests.conftest import FakeHttpClient, make_session

DASHBOARD_HTML = """
<el-menu id="menu-0" role="menu">
  <a href="https://app.inso.pl/panel/home/eea48660-3740-11ed-a611-06dd2728d782" id="i1">
    <div style="background-color: #FCCC34;"><span>FK</span></div>
    <span>Franek</span>
  </a>
  <a href="https://app.inso.pl/panel/home/a703dc5a-042d-4122-96e8-4c5111e5f7cf" id="i2">
    <div style="background-color: #A9CC63;"><span>Z</span></div>
    <span>Zofia</span>
  </a>
</el-menu>
"""


def test_list_children_returns_two_children_from_dashboard() -> None:
    Response(status=200, body=DASHBOARD_HTML.encode("utf-8"), headers=())
    client = FakeHttpClient([(200, DASHBOARD_HTML.encode("utf-8"), [])])
    cfg = Config()

    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]
    assert isinstance(result, Ok)
    assert len(result.value) == 2
    assert result.value[0].first_name == "Franek"
    assert result.value[1].first_name == "Zofia"
    assert client.calls[0]["path"] == "/panel/home/eea48660-3740-11ed-a611-06dd2728d782/"


def test_list_children_sends_phpsessid_cookie() -> None:
    client = FakeHttpClient([(200, DASHBOARD_HTML.encode("utf-8"), [])])
    cfg = Config()
    asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]
    headers = client.calls[0].get("headers") or {}
    cookie = headers.get("Cookie", "")
    assert "PHPSESSID=d9f828d2629433b8d1b9690a17d477e3" in cookie


def test_list_children_empty_is_ok() -> None:
    """A dashboard with only structureless anchors is a valid (empty)
    user state, not an error."""
    html = '<a href="https://app.inso.pl/panel/home/eea48660-3740-11ed-a611-06dd2728d782"><span>Podsumowanie</span></a>'
    client = FakeHttpClient([(200, html.encode("utf-8"), [])])
    cfg = Config()
    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]
    assert isinstance(result, Ok)
    assert result.value == []


def test_list_children_follows_dashboard_redirect() -> None:
    """The platform 301s the trailing-slash dashboard URL (observed in
    real traffic); the shell follows it same-origin and parses."""
    dashboard = DASHBOARD_HTML.encode("utf-8")
    redirect = (
        301,
        b"",
        [("location", "https://app.inso.pl/panel/home/eea48660-3740-11ed-a611-06dd2728d782")],
    )
    client = FakeHttpClient([redirect, (200, dashboard, [])])
    cfg = Config()

    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]

    assert isinstance(result, Ok)
    assert len(result.value) == 2
    assert client.calls[1]["path"] == "/panel/home/eea48660-3740-11ed-a611-06dd2728d782"
    headers = client.calls[1].get("headers") or {}
    assert "PHPSESSID=" in headers.get("Cookie", "")


def test_list_children_follows_relative_redirect() -> None:
    dashboard = DASHBOARD_HTML.encode("utf-8")
    redirect = (302, b"", [("Location", "/panel/home/eea48660-3740-11ed-a611-06dd2728d782")])
    client = FakeHttpClient([redirect, (200, dashboard, [])])
    cfg = Config()

    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]

    assert isinstance(result, Ok)
    assert client.calls[1]["path"] == "/panel/home/eea48660-3740-11ed-a611-06dd2728d782"


def test_list_children_refuses_off_domain_redirect() -> None:
    redirect = (301, b"", [("location", "https://evil.example/panel/home/x")])
    client = FakeHttpClient([redirect])
    cfg = Config()

    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.HTTP
    assert result.error.subject == "off_domain_redirect"


def test_list_children_surfaces_redirect_loop() -> None:
    redirect = (301, b"", [("location", "/panel/home/eea48660-3740-11ed-a611-06dd2728d782/")])
    client = FakeHttpClient([redirect] * 7)
    cfg = Config()

    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.HTTP
    assert result.error.subject == "redirect_loop"


def test_list_children_login_redirect_is_session_expired() -> None:
    """An expired PHPSESSID makes the dashboard 302 to /login (observed
    live: logged-out sync → GET /panel/home/<uuid>/ → 302 → /login).
    The shell must report SESSION_EXPIRED without fetching the login
    page — parsing it would misreport as markup drift."""
    redirect = (302, b"", [("Location", "/login")])
    client = FakeHttpClient([redirect, (200, b"<html>login page</html>", [])])
    cfg = Config()

    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.SESSION_EXPIRED
    assert result.error.subject == "children_list"
    assert len(client.calls) == 1  # the login page is never requested


def test_list_children_login_redirect_with_query_is_session_expired() -> None:
    """A redirect to /login?<params> is the same signal."""
    redirect = (302, b"", [("Location", "/login?returnUrl=/panel/home/x")])
    client = FakeHttpClient([redirect])
    cfg = Config()

    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]

    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.SESSION_EXPIRED


def test_list_children_propagates_http_err() -> None:
    err = CliError(kind=CliErrorKind.HTTP, subject="connect")
    client = FakeHttpClient([Err(err)])
    cfg = Config()
    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.HTTP
