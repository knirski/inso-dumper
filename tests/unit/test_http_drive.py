"""Unit tests for the drive walk shell (http/drive.py walk_drive)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind
from tests.conftest import make_session


class FakeHttpClientScript:
    """Script-based fake client for shell tests (calls recorded)."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        form: Any = None,
        headers: Any = None,
    ) -> Any:
        self.calls.append({"method": method, "path": path, "form": form, "headers": headers})
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        pass


def _response(status: int, body: bytes) -> Any:
    from inso_dumper.http.client import Response

    return Ok(Response(status=status, body=body, headers=()))


def _listing(
    dirs: list[dict[str, Any]] | None = None,
    *,
    breadcrumbs: list[dict[str, Any]] | None = None,
) -> bytes:
    return json.dumps(
        {
            "breadcrumbs": breadcrumbs if breadcrumbs is not None else [{"name": "Pliki", "id": None}],
            "directories": dirs or [],
            "directory": None,
            "files": [],
        }
    ).encode()


def _walk(client: FakeHttpClientScript) -> list[tuple[str, list[str]]]:
    from inso_dumper.http.drive import walk_drive

    async def _run() -> list[tuple[str, list[str]]]:
        seen: list[tuple[str, list[str]]] = []
        async for item in walk_drive(client, make_session()):
            match item:
                case Err(error):
                    raise AssertionError(f"unexpected error: {error}")
                case Ok((path, listing)):
                    seen.append((path, [bc.name for bc in listing.breadcrumbs]))
        return seen

    return asyncio.run(_run())


def test_walk_drive_nested_dfs_order() -> None:
    root = _listing([{"id": "d1", "name": "Zdjęcia"}, {"id": "d2", "name": "Dokumenty"}])
    d1 = _listing([{"id": "d3", "name": "2026"}])
    d2 = _listing([])
    d3 = _listing([])
    client = FakeHttpClientScript(
        [_response(200, root), _response(200, d1), _response(200, d3), _response(200, d2)]
    )
    seen = _walk(client)
    assert [path for path, _ in seen] == ["", "zdjecia", "zdjecia/2026", "dokumenty"]
    expected_paths = [
        "/drive/items",
        "/drive/items/d1",
        "/drive/items/d3",
        "/drive/items/d2",
    ]
    assert [c["path"] for c in client.calls] == expected_paths
    headers = client.calls[0].get("headers") or {}
    assert "PHPSESSID=" in headers.get("Cookie", "")


def test_walk_drive_empty_drive_yields_root_only() -> None:
    client = FakeHttpClientScript([_response(200, _listing())])
    seen = _walk(client)
    assert len(seen) == 1
    assert seen[0][0] == ""


def test_walk_drive_directory_missing_id_is_drive_node_error() -> None:
    client = FakeHttpClientScript([_response(200, _listing([{"name": "no-id"}]))])
    from inso_dumper.http.drive import walk_drive

    async def _run() -> list[Any]:
        return [item async for item in walk_drive(client, make_session())]

    items = asyncio.run(_run())
    assert len(items) == 2  # root listing, then the node error
    assert isinstance(items[1], Err)
    assert items[1].error.kind is CliErrorKind.PLATFORM_CHANGED
    assert items[1].error.subject == "drive_node"


def test_walk_drive_directory_blank_name_is_drive_node_error() -> None:
    client = FakeHttpClientScript([_response(200, _listing([{"id": "d1", "name": "  "}, {"id": "d2", "name": "ok"}]))])

    from inso_dumper.http.drive import walk_drive

    async def _run() -> list[Any]:
        return [item async for item in walk_drive(client, make_session())]

    items = asyncio.run(_run())
    assert isinstance(items[1], Err)
    assert items[1].error.subject == "drive_node"
    assert len(client.calls) == 1  # node validated before any descent request


def test_walk_drive_non_200_is_http_error() -> None:
    client = FakeHttpClientScript([_response(500, b"boom")])

    from inso_dumper.http.drive import walk_drive

    async def _run() -> list[Any]:
        return [item async for item in walk_drive(client, make_session())]

    items = asyncio.run(_run())
    assert len(items) == 1
    assert isinstance(items[0], Err)
    assert items[0].error.kind is CliErrorKind.HTTP
    assert items[0].error.subject == "drive_status_500"


def test_walk_drive_client_err_propagates() -> None:
    err = Err(CliError(kind=CliErrorKind.HTTP, subject="connect"))
    client = FakeHttpClientScript([err])

    from inso_dumper.http.drive import walk_drive

    async def _run() -> list[Any]:
        return [item async for item in walk_drive(client, make_session())]

    items = asyncio.run(_run())
    assert len(items) == 1
    assert isinstance(items[0], Err)
    assert items[0].error.subject == "connect"


def test_walk_drive_slugifies_path_components() -> None:
    """Platform-supplied names cannot escape dump/documents/: a name
    containing '/', backslash, or '..' must slugify to one safe segment
    (design.md sync-flow sanitization rule)."""
    root = _listing([{"id": "d1", "name": "../escape"}, {"id": "d2", "name": "a/b"}])
    d1 = _listing([])
    d2 = _listing([])
    client = FakeHttpClientScript(
        [_response(200, root), _response(200, d1), _response(200, d2)]
    )
    seen = _walk(client)
    paths = [path for path, _ in seen]
    assert "escape" in paths
    assert "a-b" in paths
    for path in paths:
        for segment in path.split("/"):
            if segment:
                assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in segment), segment


def test_walk_drive_never_requests_write_endpoints() -> None:
    root = _listing([{"id": "d1", "name": "x"}])
    d1 = _listing([])
    client = FakeHttpClientScript([_response(200, root), _response(200, d1)])
    _walk(client)
    for call in client.calls:
        assert not call["path"].startswith(("/drive/directory", "/drive/file"))
