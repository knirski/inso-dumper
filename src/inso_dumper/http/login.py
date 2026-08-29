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
from urllib.parse import urljoin

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.auth import build_login_payload, parse_login_response
from inso_dumper.http.client import HttpClient, Response
from inso_dumper.models.session import Session

# Maximum number of redirects to follow before giving up. The spike's
# observed chain is /login -> /panel/ -> /panel/home/<uuid>/, i.e. 2.
_MAX_REDIRECTS = 5


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

    # 2. POST /login with the form payload.
    post = await client.request("POST", "/login", form=build_login_payload(email, password))
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
        # Same-host redirects only; if the server sends us off-domain
        # we treat that as a 200 final and let the parser decide.
        if not last_url.startswith(base):
            break
        path = last_url[len(base) :] or "/"
        next_resp = await client.request("GET", path)
        match next_resp:
            case Err(error):
                return Err(error)
            case Ok(response):
                current = response
    else:
        # Loop exhausted without a 200 final response.
        return Err(CliError(kind=CliErrorKind.AUTH, subject="redirect_loop"))

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


__all__ = ["login"]
