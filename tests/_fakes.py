"""Shared test fixtures and constants.

The two constants below come from the discovery spike at
``docs/api-notes.md`` and are referenced from 19+ test sites. Keeping
them in one place means a future spec that changes the spike
representation only edits this file.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from inso_dumper.http.client import Response
from inso_dumper.models.session import Session

# Real values from the discovery spike (docs/api-notes.md).
SPIKE_PHPSESSID = "d9f828d2629433b8d1b9690a17d477e3"
SPIKE_USER_UUID = "eea48660-3740-11ed-a611-06dd2728d782"


def make_session(
    phpsessid: str = SPIKE_PHPSESSID,
    user_uuid: str = SPIKE_USER_UUID,
) -> Session:
    """Return a Session with spike defaults; override for the few tests that need it."""
    return Session(phpsessid=phpsessid, user_uuid=user_uuid)


def make_post_dict(**overrides: Any) -> dict[str, Any]:
    """A minimal but complete API-shaped timeline post payload (camelCase)."""
    base: dict[str, Any] = {
        "id": "3d5b38b4-6c0b-4e79-8e55-375272c76779",
        "title": "Wycieczka",
        "content": "<p>hello</p>",
        "media": {"photos": [], "videos": [], "attachments": []},
        "createdAt": 1787811431,
        "createdAtText": "czwartek,  8:17",
        "visibleFor": ["Zółta"],
        "visibleForChildren": [],
        "actions": {"vote": "/v", "unvote": "/u"},
        "sticky": False,
        "archived": False,
        "userVoted": False,
        "author": "Lipińska Agnieszka",
        "likes": 0,
        "likesText": "0 polubień",
        "commentsAvailable": False,
        "comments": 0,
        "commentsText": "0 komentarzy",
        "isWorker": False,
        "isWorkerOnly": False,
        "displayed": True,
        "poll": None,
        "translations": [],
    }
    base.update(overrides)
    return base


def make_photo_dict(name: str = "IMG_1.jpeg") -> dict[str, Any]:
    return {
        "type": "photo",
        "name": name,
        "src": {
            "thumb": "https://file.inso.pl/t/1/thumb.jpg?Expires=1",
            "full": f"https://file.inso.pl/t/1/{name}/full.jpg?Expires=1",
        },
    }


def make_video_dict(name: str = "klip.mp4") -> dict[str, Any]:
    return {
        "type": "video",
        "name": name,
        "src": {
            "thumb": "https://file.inso.pl/t/1/thumb.jpg?Expires=1",
            "full": f"https://file.inso.pl/t/1/{name}/full.mp4?Expires=1",
        },
    }


def make_attachment_dict(name: str = "dokument.pdf") -> dict[str, Any]:
    return {"name": name, "url": f"https://file.inso.pl/t/1/{name}?Expires=1"}


class FakeHttpClient:
    """A minimal in-memory HttpClient for shell tests.

    Programmable via the ``script`` constructor argument: each call to
    ``request`` pops the next item. Each item is one of:

    - ``(status, body, headers)``: respond with a Response and Ok(...)
    - ``CliError`` instance: respond with Err(...)
    - an ``Exception`` instance: raise it (used for transport crashes)

    The lifecycle method is ``aclose`` (matches the real HttpxClient).
    Tests that need to verify the CLI's async-with usage should call
    ``async with FakeHttpClient(...) as client:``; __aexit__ is a
    no-op that returns None.
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
    ) -> Any:
        self.calls.append({"method": method, "path": path, "form": form, "headers": headers})
        if not self._script:
            raise AssertionError(f"unexpected call: {method} {path}")
        next_item = self._script.pop(0)
        if isinstance(next_item, BaseException):
            raise next_item
        if isinstance(next_item, Err) or (
            hasattr(next_item, "error") and not hasattr(next_item, "value")
        ):
            return next_item
        status, body, headers_list = next_item
        return Ok(
            Response(
                status=status,
                body=body,
                headers=tuple(headers_list),
            )
        )

    async def aclose(self) -> None:
        pass

    async def __aenter__(self) -> FakeHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()


# Local import for the type-only reference; the runtime usage above
# avoids the circular import.
from inso_dumper._result import Err, Ok  # noqa: E402
