"""Unit tests for the login shell."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.auth import build_login_payload
from inso_dumper.http.client import Response
from inso_dumper.http.login import login


class _FakeClient:
    """A programmable HttpClient for shell tests.

    Each call to ``request`` pops the next response from ``script``.
    A response value is a tuple ``(status, body, headers_tuple)`` or
    an ``Exception`` instance to raise.
    """

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Ok[Response] | Err[Any]:
        self.calls.append({"method": method, "path": path, "form": form})
        if not self._script:
            raise AssertionError(f"unexpected call: {method} {path}")
        next_item = self._script.pop(0)
        if isinstance(next_item, BaseException):
            raise next_item
        status, body, headers_list = next_item
        return Ok(
            Response(
                status=status,
                body=body,
                headers=tuple(headers_list),
            )
        )

    def close(self) -> None:  # mirror HttpxClient API surface
        pass


def _ok(status: int, body: bytes = b"", headers: Sequence[tuple[str, str]] = ()) -> Any:
    return (status, body, headers)


def test_login_follows_redirect_chain_and_emits_session() -> None:
    cookie = "PHPSESSID=d9f828d2629433b8d1b9690a17d477e3; path=/; HttpOnly"
    client = _FakeClient(
        [
            _ok(200, b"<html>login form</html>"),
            _ok(302, b"", (("Location", "/panel/"), ("Set-Cookie", cookie))),
            _ok(302, b"", (("Location", "/panel/home/eea48660-3740-11ed-a611-06dd2728d782/"),)),
            _ok(200, b"<html>dashboard</html>"),
        ]
    )
    cfg = Config()

    import asyncio

    result = asyncio.run(
        login(client, cfg, "user@example.com", "hunter2")  # type: ignore[arg-type]
    )
    assert isinstance(result, Ok)
    assert result.value.phpsessid == "d9f828d2629433b8d1b9690a17d477e3"
    assert result.value.user_uuid == "eea48660-3740-11ed-a611-06dd2728d782"

    # Four request calls: GET /login, POST /login, GET /panel/,
    # GET /panel/home/<uuid>/.
    assert len(client.calls) == 4
    assert client.calls[0]["method"] == "GET"
    assert client.calls[1]["method"] == "POST"
    assert client.calls[1]["form"] == build_login_payload("user@example.com", "hunter2")
    assert client.calls[2]["path"] == "/panel/"
    assert client.calls[3]["path"] == "/panel/home/eea48660-3740-11ed-a611-06dd2728d782/"


def test_login_short_circuits_on_http_err() -> None:
    """An Err from the client propagates without raising."""
    from inso_dumper.errors import CliError

    err = CliError(kind=CliErrorKind.HTTP, subject="connect")
    cfg = Config()

    import asyncio

    # Wrap the fake to return Err instead of raising.
    class _ErrClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def request(
            self,
            method: str,
            path: str,
            *,
            form: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None,
        ) -> Any:
            self.calls.append({"method": method, "path": path})
            return Err(err)

        def close(self) -> None:
            pass

    eclient = _ErrClient()
    result = asyncio.run(login(eclient, cfg, "u@x", "p"))  # type: ignore[arg-type]
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.HTTP
    assert result.error.subject == "connect"


def test_login_returns_auth_err_when_no_phpsessid_in_chain() -> None:
    client = _FakeClient(
        [
            _ok(200),
            _ok(302, b"", (("Location", "/panel/"),)),  # no Set-Cookie
            _ok(200),
        ]
    )
    cfg = Config()

    import asyncio

    result = asyncio.run(login(client, cfg, "u@x", "p"))  # type: ignore[arg-type]
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.AUTH


def test_login_stops_after_max_redirects() -> None:
    """A redirect loop must not hang the CLI; exit kind is HTTP, not AUTH."""
    client = _FakeClient(
        [
            _ok(200),
            *(_ok(302, b"", (("Location", "/panel/"),)) for _ in range(10)),
        ]
    )
    cfg = Config()

    import asyncio

    result = asyncio.run(login(client, cfg, "u@x", "p"))  # type: ignore[arg-type]
    assert isinstance(result, Err)
    # A redirect storm is a server-side / protocol anomaly, not bad
    # credentials — exit kind must be HTTP, not AUTH.
    assert result.error.kind is CliErrorKind.HTTP
    assert result.error.subject == "redirect_loop"


def test_login_no_raise_statements() -> None:
    """AGENTS.md: only the single try/except is allowed at the boundary."""
    import inspect

    from inso_dumper.http import login as mod

    source = inspect.getsource(mod)
    # A single try/except is allowed; we just want to assert that the
    # shell does not re-raise. The "no raise" check is on the parsers.
    assert "raise " not in source.replace("raise SystemExit", "")
