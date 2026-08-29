"""Unit tests for the login shell."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliError, CliErrorKind
from inso_dumper.http.auth import build_login_payload
from inso_dumper.http.login import _is_same_origin, login
from tests.conftest import FakeHttpClient


def _ok(status: int, body: bytes = b"", headers: list[tuple[str, str]] | None = None) -> Any:
    return (status, body, headers or [])


def test_login_follows_redirect_chain_and_emits_session() -> None:
    cookie = "PHPSESSID=d9f828d2629433b8d1b9690a17d477e3; path=/; HttpOnly"
    client = FakeHttpClient(
        [
            _ok(200, b"<html>login form</html>"),
            _ok(302, b"", [("Location", "/panel/"), ("Set-Cookie", cookie)]),
            _ok(302, b"", [("Location", "/panel/home/eea48660-3740-11ed-a611-06dd2728d782/")]),
            _ok(200, b"<html>dashboard</html>"),
        ]
    )
    cfg = Config()

    async def run() -> Any:
        return await login(client, cfg, "user@example.com", "hunter2")  # type: ignore[arg-type]

    result = asyncio.run(run())
    assert isinstance(result, Ok)
    assert result.value.phpsessid == "d9f828d2629433b8d1b9690a17d477e3"
    assert result.value.user_uuid == "eea48660-3740-11ed-a611-06dd2728d782"

    assert len(client.calls) == 4
    assert client.calls[0]["method"] == "GET"
    assert client.calls[1]["method"] == "POST"
    assert client.calls[1]["form"] == build_login_payload("user@example.com", "hunter2")
    assert client.calls[2]["path"] == "/panel/"
    assert client.calls[3]["path"] == "/panel/home/eea48660-3740-11ed-a611-06dd2728d782/"


def test_login_short_circuits_on_http_err() -> None:
    err = CliError(kind=CliErrorKind.HTTP, subject="connect")
    cfg = Config()

    async def run() -> Any:
        async with FakeHttpClient([Err(err)]) as client:
            return await login(client, cfg, "u@x", "p")  # type: ignore[arg-type]

    result = asyncio.run(run())
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.HTTP
    assert result.error.subject == "connect"


def test_login_returns_auth_err_when_no_phpsessid_in_chain() -> None:
    client = FakeHttpClient(
        [
            _ok(200),
            _ok(302, b"", [("Location", "/panel/")]),  # no Set-Cookie
            _ok(200),
        ]
    )
    cfg = Config()
    result = asyncio.run(login(client, cfg, "u@x", "p"))  # type: ignore[arg-type]
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.AUTH


def test_login_stops_after_max_redirects() -> None:
    """A redirect storm must not hang; exit kind is HTTP, not AUTH."""
    client = FakeHttpClient(
        [
            _ok(200),
            *(_ok(302, b"", [("Location", "/panel/")]) for _ in range(10)),
        ]
    )
    cfg = Config()
    result = asyncio.run(login(client, cfg, "u@x", "p"))  # type: ignore[arg-type]
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.HTTP
    assert result.error.subject == "redirect_loop"


def test_login_off_domain_redirect_returns_http() -> None:
    """A server-side redirect to a different origin is a transport anomaly."""
    client = FakeHttpClient(
        [
            _ok(200),
            _ok(302, b"", [("Location", "https://app.inso.pl.attacker.tld/path")]),
        ]
    )
    cfg = Config()
    result = asyncio.run(login(client, cfg, "u@x", "p"))  # type: ignore[arg-type]
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.HTTP
    assert result.error.subject == "off_domain_redirect"


def test_is_same_origin_uses_urlparse() -> None:
    """A naive startswith would accept app.inso.pl.attacker.tld; urlparse does not."""
    base = "https://app.inso.pl"
    assert _is_same_origin("https://app.inso.pl/panel/", base) is True
    assert _is_same_origin("http://app.inso.pl/x", base) is False
    assert _is_same_origin("https://app.inso.pl.attacker.tld/x", base) is False
    assert _is_same_origin("https://attacker.tld/x", base) is False


def test_login_no_raise_statements() -> None:
    """AGENTS.md: the shell converts transport errors to CliError at the boundary."""
    import inspect

    from inso_dumper.http import login as mod

    source = inspect.getsource(mod)
    # A regex anchored at line start: don't false-positive on words
    # like 'raise the bar' in a docstring.
    assert not re.search(r"^\s*raise\b", source, re.MULTILINE), (
        "login shell must not raise; transport errors are converted at the boundary"
    )
