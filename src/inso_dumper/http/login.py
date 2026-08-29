"""Login shell: orchestrate the GET warm-up, POST, and redirect follow.

This is the only function in the codebase that does the login network
flow. The two pure parsers in ``inso_dumper.http.auth`` do the
decoding.

Contract:
  - Returns ``CliResult[Session]``.
  - Never raises; transport errors are converted at the boundary.
  - The single ``try/except`` here is the only one in the shell layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urljoin, urlparse

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.auth import build_login_payload, parse_login_response
from inso_dumper.http.client import HttpClient, Response
from inso_dumper.models.session import Session

# Maximum number of redirects to follow before giving up. The spike's
# observed chain is /login -> /panel/ -> /panel/home/<uuid>/, i.e. 2;
# 5 is a 2.5x safety margin that bounds a redirect-storm DoS in
# seconds, not unbounded.
_MAX_REDIRECTS = 5


def _is_same_origin(url: str, base: str) -> bool:
    """True when ``url`` resolves to the same scheme+host+port as ``base``.

    A naive ``startswith(base)`` accepts ``https://app.inso.pl.attacker.tld``
    as a prefix of ``https://app.inso.pl``; that sends the Cloudflare-
    fingerprint headers to a host derived from an attacker-supplied
    string. urlparse compares the parsed netlocs instead.
    """
    base_parsed = urlparse(base)
    url_parsed = urlparse(url)
    return (
        url_parsed.scheme == base_parsed.scheme
        and (url_parsed.hostname or "").lower() == (base_parsed.hostname or "").lower()
        and (url_parsed.port or base_parsed.port) == (base_parsed.port or url_parsed.port)
    )


async def login(
    client: HttpClient,
    config: Config,
    email: str,
    password: str,
) -> CliResult[Session]:
    """Drive the Inso login flow and return the resulting Session.

    1. GET /login (warm-up; harmless if the server already has cookies).
    2. POST /login with the form payload.
    3. Follow ``Location`` redirects until the URL matches
       ``/panel/home/<uuid>/`` or the chain exceeds ``_MAX_REDIRECTS``.
       A redirect to a different origin is treated as a server anomaly
       and surfaces as ``Err(HTTP, off_domain_redirect)``.
    4. Parse the final response for PHPSESSID and the user UUID.
    """
    base = str(config.base_url).rstrip("/")

    # 1. Warm-up GET. Failures here are non-fatal: a redirect or a 200
    # login page is what we want; transport errors short-circuit.
    warmup = await client.request("GET", "/login")
    match warmup:
        case Err(error):
            return Err(error)
        case Ok(_):
            pass

    # 2. POST /login with the form payload. The Inso platform requires
    # Origin and Referer to match the configured base URL (verified in
    # docs/api-notes.md "Auth model"); httpx with the browser-like
    # header set alone is rejected.
    origin = base
    referer = f"{base}/login"
    post = await client.request(
        "POST",
        "/login",
        form=build_login_payload(email, password),
        headers={"Origin": origin, "Referer": referer},
    )
    match post:
        case Err(error):
            return Err(error)
        case Ok(response):
            current = response

    # 3. Follow the redirect chain. Each step consumes the Location
    # header from the previous response and accumulates Set-Cookie
    # values so the parser sees the final state.
    last_url = f"{base}/login"
    accumulated_cookies: list[tuple[str, str]] = []
    for _ in range(_MAX_REDIRECTS):
        # Capture any Set-Cookie headers from the current response.
        for name, value in current.headers:
            if name.lower() == "set-cookie":
                accumulated_cookies.append((name, value))
        if current.status not in (301, 302, 303, 307, 308):
            break
        location = _header_value(current.headers, "location")
        if not location:
            break
        # Resolve relative locations against the last URL.
        last_url = urljoin(last_url, location)
        # Off-domain redirect is a server anomaly: return HTTP so the
        # user is told the platform sent us somewhere unexpected.
        if not _is_same_origin(last_url, base):
            return Err(CliError(kind=CliErrorKind.HTTP, subject="off_domain_redirect"))
        # Strip the base prefix; handle the empty case explicitly.
        path = last_url[len(base) :]
        if not path:
            path = "/"
        next_resp = await client.request("GET", path)
        match next_resp:
            case Err(error):
                return Err(error)
            case Ok(response):
                current = response
    else:
        # Loop exhausted without a 200 final response. The server is
        # misbehaving (a redirect storm is closer to a transport /
        # protocol anomaly than to bad credentials), so this is HTTP.
        return Err(CliError(kind=CliErrorKind.HTTP, subject="redirect_loop"))

    # 4. Build a synthetic final response that includes the accumulated
    # Set-Cookie headers, then decode.
    final_response = Response(
        status=current.status,
        body=current.body,
        headers=tuple(list(current.headers) + accumulated_cookies),
    )
    return parse_login_response(final_response, final_url=last_url)


def _header_value(headers: Sequence[tuple[str, str]], name: str) -> str | None:
    needle = name.lower()
    for k, v in headers:
        if k.lower() == needle:
            return v
    return None


__all__ = ["_is_same_origin", "login"]
