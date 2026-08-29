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

from urllib.parse import urljoin

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.auth import build_login_payload, parse_login_response
from inso_dumper.http.client import HttpClient, Response
from inso_dumper.http.redirects import (
    MAX_REDIRECTS,
    REDIRECT_STATUSES,
    header_value,
    is_same_origin,
)
from inso_dumper.models.session import Session


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
       ``/panel/home/<uuid>/`` or the chain exceeds ``MAX_REDIRECTS``.
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
    for _ in range(MAX_REDIRECTS):
        # Capture any Set-Cookie headers from the current response.
        for name, value in current.headers:
            if name.lower() == "set-cookie":
                accumulated_cookies.append((name, value))
        if current.status not in REDIRECT_STATUSES:
            break
        location = header_value(current.headers, "location")
        if not location:
            break
        # Resolve relative locations against the last URL.
        last_url = urljoin(last_url, location)
        # Off-domain redirect is a server anomaly: return HTTP so the
        # user is told the platform sent us somewhere unexpected.
        if not is_same_origin(last_url, base):
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
        # The for-else fires only when the loop completed without
        # breaking — i.e. all MAX_REDIRECTS iterations ran end to end.
        # At that point `current` is the response fetched on the last
        # allowed redirect. If it is itself a followable redirect, the
        # server is in a loop and we surface HTTP. If it is the final
        # response (e.g. HTTP 200), fall through to the parser.
        if current.status in REDIRECT_STATUSES and header_value(current.headers, "location"):
            return Err(CliError(kind=CliErrorKind.HTTP, subject="redirect_loop"))

    # 4. Build a synthetic final response that includes the accumulated
    # Set-Cookie headers, then decode.
    final_response = Response(
        status=current.status,
        body=current.body,
        headers=tuple(list(current.headers) + accumulated_cookies),
    )
    return parse_login_response(final_response, final_url=last_url)


__all__ = ["is_same_origin", "login"]
