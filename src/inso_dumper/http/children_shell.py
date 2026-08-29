"""list_children shell: fetch dashboard, parse the sidebar, return Child list."""

from __future__ import annotations

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.children import parse_children_list
from inso_dumper.http.client import HttpClient
from inso_dumper.http.redirects import (
    MAX_REDIRECTS,
    REDIRECT_STATUSES,
    header_value,
    resolve_location,
)
from inso_dumper.models.children import Child
from inso_dumper.models.session import Session


async def list_children(
    client: HttpClient, config: Config, session: Session
) -> CliResult[list[Child]]:
    """GET the dashboard, parse the children menu, return the list.

    Redirect responses are followed (bounded, same-origin only): the
    platform 301s the trailing-slash dashboard URL, and ``HttpxClient``
    deliberately does not auto-follow. A successful fetch that yields an
    empty menu is ``Ok([])`` — that is a valid user state, not a parser
    failure. ``Err(PLATFORM_CHANGED)`` is reserved for the parser being
    unable to find the menu at all.

    The shell returns the client's Err unchanged; no translation happens
    at this layer. Mapping Err kinds to exit codes is the CLI's job.
    """
    base = str(config.base_url).rstrip("/")
    path = f"/panel/home/{session.user_uuid}/"
    headers = {"Cookie": f"PHPSESSID={session.phpsessid}"}
    result = await client.request("GET", path, headers=headers)
    current_url = f"{base}{path}"
    response = None
    match result:
        case Err(error):
            return Err(error)
        case Ok(value):
            response = value

    for _ in range(MAX_REDIRECTS):
        if response.status not in REDIRECT_STATUSES:
            break
        location = header_value(response.headers, "location")
        if not location:
            break
        next_url = resolve_location(current_url, base, location)
        if next_url is None:
            return Err(CliError(kind=CliErrorKind.HTTP, subject="off_domain_redirect"))
        current_url = next_url
        next_path = next_url[len(base) :] or "/"
        result = await client.request("GET", next_path, headers=headers)
        match result:
            case Err(error):
                return Err(error)
            case Ok(value):
                response = value
    else:
        if response.status in REDIRECT_STATUSES and header_value(response.headers, "location"):
            return Err(CliError(kind=CliErrorKind.HTTP, subject="redirect_loop"))

    return parse_children_list(response.text())


__all__ = ["list_children"]
