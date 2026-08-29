"""list_children shell: fetch dashboard, parse the sidebar, return Child list."""

from __future__ import annotations

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config
from inso_dumper.errors import CliErrorKind, CliResult
from inso_dumper.http.children import parse_children_list
from inso_dumper.http.client import HttpClient
from inso_dumper.models.children import Child
from inso_dumper.models.session import Session


async def list_children(
    client: HttpClient, config: Config, session: Session
) -> CliResult[list[Child]]:
    """GET the dashboard, parse the children menu, return the list.

    A successful fetch that yields an empty menu is ``Ok([])`` — that
    is a valid user state, not a parser failure. ``Err(PLATFORM_CHANGED)``
    is reserved for the parser being unable to find the menu at all.
    """
    path = f"/panel/home/{session.user_uuid}/"
    headers = {"Cookie": f"PHPSESSID={session.phpsessid}"}
    result = await client.request("GET", path, headers=headers)
    match result:
        case Err(error):
            # Map the HTTP kind explicitly to keep the public contract;
            # pass through other Err kinds unchanged.
            if error.kind is CliErrorKind.HTTP:
                return Err(error)
            return Err(error)
        case Ok(response):
            return parse_children_list(response.text())


__all__ = ["list_children"]
