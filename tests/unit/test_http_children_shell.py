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
    <div style="background-color: #FCCC34;"><span>IG</span></div>
    <span>Ignacy</span>
  </a>
  <a href="https://app.inso.pl/panel/home/a703dc5a-042d-4122-96e8-4c5111e5f7cf" id="i2">
    <div style="background-color: #A9CC63;"><span>LI</span></div>
    <span>Liliana</span>
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
    assert result.value[0].first_name == "Ignacy"
    assert result.value[1].first_name == "Liliana"
    assert client.calls[0]["path"] == "/panel/home/eea48660-3740-11ed-a611-06dd2728d782/"


def test_list_children_sends_phpsessid_cookie() -> None:
    client = FakeHttpClient([(200, DASHBOARD_HTML.encode("utf-8"), [])])
    cfg = Config()
    asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]
    headers = client.calls[0].get("headers") or {}
    cookie = headers.get("Cookie", "")
    assert "PHPSESSID=d9f828d2629433b8d1b9690a17d477e3" in cookie


def test_list_children_empty_is_ok() -> None:
    """An empty menu is a valid user state, not an error."""
    html = '<el-menu id="menu-0" role="menu"></el-menu>'
    client = FakeHttpClient([(200, html.encode("utf-8"), [])])
    cfg = Config()
    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]
    assert isinstance(result, Ok)
    assert result.value == []


def test_list_children_propagates_http_err() -> None:
    err = CliError(kind=CliErrorKind.HTTP, subject="connect")
    client = FakeHttpClient([Err(err)])
    cfg = Config()
    result = asyncio.run(list_children(client, cfg, make_session()))  # type: ignore[arg-type]
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.HTTP
