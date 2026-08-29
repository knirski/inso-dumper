"""HTTP transport: HttpClient Protocol, Response, HttpxClient.

Functional-core/imperative-shell split:
  - ``Response`` and the ``HttpClient`` Protocol are core data shapes.
  - ``HttpxClient`` is the imperative shell that opens sockets.
  - ``BROWSER_LIKE_HEADERS`` is a core constant: every outbound request
    must carry this minimum set or Cloudflare's bot management returns
    403 (verified in the discovery spike; see docs/api-notes.md).

The ``HttpClient.request`` contract returns ``CliResult[Response]``: a
non-2xx status is a wrapped Response, never an error at this layer.
Transport-level failures (httpx.HTTPError, OSError) become
``Err(CliError(HTTP, subject))`` at the boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

import httpx

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliError, CliErrorKind, CliResult

# Header set required to pass Cloudflare's bot management front door.
# Verified by the discovery spike; do not change without re-testing.
# See docs/api-notes.md "Cloudflare bot management".
BROWSER_LIKE_HEADERS: Mapping[str, str] = MappingProxyType(
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "sec-ch-ua": '"Not?A_Brand";v="24", "Chromium";v="152"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
        "Upgrade-Insecure-Requests": "1",
    }
)


@dataclass(frozen=True, slots=True)
class Response:
    """Opaque transport-agnostic response wrapper.

    ``headers`` is a Sequence of (name, value) pairs to preserve duplicate
    ``Set-Cookie`` entries that the standard library's case-insensitive
    mapping would collapse.
    """

    status: int
    body: bytes
    headers: Sequence[tuple[str, str]]

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpClient(Protocol):
    """The v1 contract for any HTTP transport.

    Returns ``CliResult[Response]`` so callers never need to ``try``/``except``
    around the boundary; transport failures are mapped to
    ``Err(CliError(HTTP, subject))`` by the implementation.
    """

    async def request(
        self,
        method: str,
        path: str,
        *,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> CliResult[Response]: ...


class HttpxClient:
    """The v1 production transport. Opens sockets; converts transport
    errors to ``Err(CliError(HTTP))`` at the boundary.

    The client does not follow redirects automatically so the login's
    302 chain is observable by the caller; the login shell in
    ``inso_dumper.http.login`` follows them.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        timeout = httpx.Timeout(config.request_timeout_seconds)
        # follow_redirects=False so the login shell can observe 302s.
        self._client = httpx.AsyncClient(
            base_url=str(config.base_url).rstrip("/"),
            timeout=timeout,
            follow_redirects=False,
            headers=dict(BROWSER_LIKE_HEADERS),
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> CliResult[Response]:
        merged: dict[str, str] = dict(BROWSER_LIKE_HEADERS)
        if headers:
            merged.update(headers)
        try:
            if form is not None:
                response = await self._client.request(
                    method,
                    path,
                    data=dict(form),
                    headers=merged,
                )
            else:
                response = await self._client.request(
                    method, path, headers=merged
                )
        except httpx.HTTPError as exc:
            return Err(_classify_http_error(exc))
        except OSError as exc:
            return Err(_classify_os_error(exc))
        return Ok(_to_response(response))

    async def aclose(self) -> None:
        await self._client.aclose()


def _to_response(response: httpx.Response) -> Response:
    # httpx.Headers stores raw (name, value) pairs; raw tuples are bytes.
    # Decode to str so Response holds plain strings and Sequence type
    # matches.
    raw_headers = tuple(
        (name.decode("ascii"), value.decode("utf-8", errors="replace"))
        for name, value in response.headers.raw
    )
    return Response(
        status=response.status_code,
        body=response.content,
        headers=raw_headers,
    )


def _classify_http_error(exc: httpx.HTTPError) -> CliError:
    name = type(exc).__name__.lower()
    if "connect" in name:
        subject = "connect"
    elif "timeout" in name:
        subject = "timeout"
    elif "read" in name or "write" in name:
        subject = "io"
    else:
        subject = "http"
    return CliError(kind=CliErrorKind.HTTP, subject=subject)


def _classify_os_error(_exc: OSError) -> CliError:
    return CliError(kind=CliErrorKind.HTTP, subject="os")


__all__ = [
    "BROWSER_LIKE_HEADERS",
    "HttpClient",
    "HttpxClient",
    "Response",
]
