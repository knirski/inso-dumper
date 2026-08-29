"""Unit tests for the HttpClient Protocol, Response, and HttpxClient."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.client import (
    BROWSER_LIKE_HEADERS,
    HttpxClient,
    Response,
)


def test_accept_language_is_polish_primary() -> None:
    assert BROWSER_LIKE_HEADERS["Accept-Language"].startswith("pl-PL")


def test_response_text_decodes_body() -> None:
    r = Response(status=200, body=b"hello", headers=())
    assert r.text() == "hello"


def test_response_preserves_duplicate_headers() -> None:
    r = Response(
        status=200,
        body=b"",
        headers=(("Set-Cookie", "PHPSESSID=abc"), ("Set-Cookie", "REMEMBERME=deleted")),
    )
    set_cookies = [v for k, v in r.headers if k.lower() == "set-cookie"]
    assert set_cookies == ["PHPSESSID=abc", "REMEMBERME=deleted"]


def test_httpx_client_merges_browser_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorded-response test: assert the merged header set was sent."""
    sent_headers: dict[str, str] = {}

    async def fake_send(
        self: httpx.AsyncClient,
        request: httpx.Request,
        *,
        auth: Any = None,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        sent_headers.update(dict(request.headers))
        return httpx.Response(
            status_code=200,
            content=b"<html></html>",
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)

    cfg = Config()
    client = HttpxClient(cfg)

    async def run() -> None:
        result = await client.request("GET", "/login")
        assert isinstance(result, Ok)
        assert result.value.status == 200

    import asyncio

    asyncio.run(run())

    # Every required key is in the outbound request (httpx lowercases).
    for key in BROWSER_LIKE_HEADERS:
        assert key.lower() in sent_headers, f"missing from request: {key}"
    assert sent_headers["accept-language"].startswith("pl-PL")


def test_httpx_client_returns_404_as_ok_response() -> None:
    """A non-2xx status is a Response, not a transport error."""
    sent_status = 0

    def fake_send(self: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        nonlocal sent_status
        sent_status = 403
        return httpx.Response(
            status_code=403,
            content=b"blocked",
            request=request,
        )

    import asyncio

    cfg = Config()
    client = HttpxClient(cfg)

    async def run() -> None:
        # Monkeypatching the bound method requires we replace on the
        # already-constructed AsyncClient. Pull the underlying client
        # and rebind send on it.
        original_send = client._client.send  # type: ignore[attr-defined]

        async def patched_send(request: httpx.Request, **kwargs: Any) -> httpx.Response:
            return httpx.Response(status_code=403, content=b"blocked", request=request)

        client._client.send = patched_send  # type: ignore[method-assign]
        result = await client.request("GET", "/")
        assert isinstance(result, Ok)
        assert result.value.status == 403
        client._client.send = original_send  # type: ignore[method-assign]

    asyncio.run(run())


def test_httpx_client_translates_connect_error_to_err() -> None:
    import asyncio

    cfg = Config()
    client = HttpxClient(cfg)

    async def run() -> None:
        async def patched_send(request: httpx.Request, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("simulated", request=request)

        client._client.send = patched_send  # type: ignore[method-assign]
        result = await client.request("GET", "/")
        assert isinstance(result, Err)
        assert result.error.kind is CliErrorKind.HTTP

    asyncio.run(run())


def test_httpx_client_sends_form_body() -> None:
    import asyncio

    cfg = Config()
    client = HttpxClient(cfg)
    captured: list[httpx.Request] = []

    async def run() -> None:
        async def patched_send(request: httpx.Request, **kwargs: Any) -> httpx.Response:
            captured.append(request)
            return httpx.Response(status_code=302, content=b"", request=request)

        client._client.send = patched_send  # type: ignore[method-assign]
        result = await client.request("POST", "/login", form={"_username": "x", "_password": "y"})
        assert isinstance(result, Ok)

    asyncio.run(run())

    assert len(captured) == 1
    body = captured[0].content.decode("utf-8")
    assert "_username=x" in body
    assert "_password=y" in body
    form_header = captured[0].headers.get("content-type", "")
    assert "application/x-www-form-urlencoded" in form_header


def test_httpx_client_no_raise_statements() -> None:
    """The contract: no 'raise' in HttpxClient; the boundary is Result."""
    import inspect

    from inso_dumper.http import client as client_mod

    source = inspect.getsource(client_mod)
    assert "raise " not in source, "HttpxClient must not raise"


def test_httpx_client_is_async_context_manager() -> None:
    """`async with HttpxClient(cfg)` calls aclose on the same event loop.

    This is the structural fix for the 'Event loop is closed' bug that
    fires when asyncio.run is called twice with the same AsyncClient.
    The test asserts the contract by acquiring via __aenter__ and
    verifying __aexit__ invokes aclose on the same loop.
    """
    import asyncio

    cfg = Config()
    called = {"aclose": False}

    async def run() -> None:
        client = HttpxClient(cfg)
        # Patch aclose on the instance to record the call.
        original_aclose = client.aclose

        async def traced() -> None:
            called["aclose"] = True
            await original_aclose()

        client.aclose = traced  # type: ignore[method-assign]
        async with client:
            pass
        assert called["aclose"]

    asyncio.run(run())


def test_rate_limit_sleeps_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rate limit is enforced by sleeping between calls."""
    import asyncio

    from inso_dumper.config import Config

    cfg = Config(rate_limit_per_second=10.0)  # 100ms gap
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    async def fake_send(
        self: httpx.AsyncClient,
        request: httpx.Request,
        *,
        auth: Any = None,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        return httpx.Response(status_code=200, content=b"", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)

    async def run() -> None:
        client = HttpxClient(cfg)
        await client.request("GET", "/a")
        await client.request("GET", "/b")

    asyncio.run(run())
    # The second call should have slept ~0.1s.
    assert any(s > 0.05 for s in sleeps)


def test_rate_limit_disabled_when_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-positive rate limit disables throttling."""
    import asyncio

    from inso_dumper.config import Config
    from inso_dumper.http import client as client_mod

    cfg = Config(rate_limit_per_second=0.0)
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)

    async def fake_send(
        self: httpx.AsyncClient,
        request: httpx.Request,
        *,
        auth: Any = None,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        return httpx.Response(status_code=200, content=b"", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)

    async def run() -> None:
        client = HttpxClient(cfg)
        await client.request("GET", "/a")
        await client.request("GET", "/b")
        await client.request("GET", "/c")

    asyncio.run(run())
    assert sleeps == []
