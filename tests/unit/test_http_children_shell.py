"""Unit tests for the list_children shell."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.children_shell import list_children
from inso_dumper.http.client import Response
from inso_dumper.models.session import Session

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


class _FakeClient:
    def __init__(self, response: Response) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Ok[Response]:
        self.calls.append({"method": method, "path": path, "form": form, "headers": headers})
        return Ok(self._response)

    def close(self) -> None:
        pass


def _session() -> Session:
    return Session(
        phpsessid="d9f828d2629433b8d1b9690a17d477e3",
        user_uuid="eea48660-3740-11ed-a611-06dd2728d782",
    )


def test_list_children_returns_two_children_from_dashboard() -> None:
    response = Response(status=200, body=DASHBOARD_HTML.encode("utf-8"), headers=())
    client = _FakeClient(response)
    cfg = Config()

    import asyncio

    result = asyncio.run(list_children(client, cfg, _session()))  # type: ignore[arg-type]
    assert isinstance(result, Ok)
    assert len(result.value) == 2
    assert result.value[0].first_name == "Ignacy"
    assert result.value[1].first_name == "Liliana"
    assert client.calls[0]["path"] == "/panel/home/eea48660-3740-11ed-a611-06dd2728d782/"


def test_list_children_sends_phpsessid_cookie() -> None:
    response = Response(status=200, body=DASHBOARD_HTML.encode("utf-8"), headers=())
    client = _FakeClient(response)
    cfg = Config()

    import asyncio

    asyncio.run(list_children(client, cfg, _session()))  # type: ignore[arg-type]
    headers = client.calls[0].get("headers") or {}
    cookie = headers.get("Cookie", "")
    assert "PHPSESSID=d9f828d2629433b8d1b9690a17d477e3" in cookie


def test_list_children_empty_is_ok() -> None:
    """An empty menu is a valid user state, not an error."""
    html = '<el-menu id="menu-0" role="menu"></el-menu>'
    response = Response(status=200, body=html.encode("utf-8"), headers=())
    client = _FakeClient(response)
    cfg = Config()

    import asyncio

    result = asyncio.run(list_children(client, cfg, _session()))  # type: ignore[arg-type]
    assert isinstance(result, Ok)
    assert result.value == []


def test_list_children_propagates_http_err() -> None:
    err = __import__("inso_dumper.errors", fromlist=["CliError"]).CliError(
        kind=CliErrorKind.HTTP, subject="connect"
    )

    class _ErrClient:
        async def request(self, method: str, path: str, **kw: Any) -> Err[Any]:
            return Err(err)

        def close(self) -> None:
            pass

    cfg = Config()

    import asyncio

    result = asyncio.run(list_children(_ErrClient(), cfg, _session()))  # type: ignore[arg-type]
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.HTTP
