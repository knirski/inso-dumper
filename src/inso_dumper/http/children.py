"""Pure HTML → ``list[Child]`` parser for the Inso sidebar menu.

Functional core: no IO, no ``httpx``, no ``open``. The shell in
``inso_dumper.http.children_shell`` is the only caller; it pulls
``Response.text`` and passes the string in.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from html.parser import HTMLParser

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.client import Response
from inso_dumper.models.children import Child

_MENU_ID = "menu-0"
_HREF_RE = re.compile(
    r"^https://app\.inso\.pl/panel/home/(?P<uuid>[0-9a-f-]+)/?$",
    re.IGNORECASE,
)
_COLOR_RE = re.compile(r"background-color:\s*(#[0-9a-fA-F]{3,8})")


class _MenuParser(HTMLParser):
    """A streaming parser that captures each ``<a>`` child of the menu.

    The element is ``<el-menu id="menu-0">`` — not standard HTML — so a
    full HTML parser is overkill. The state machine below tracks
    ``inside_menu``, the currently-active ``<a>``, and the text and style
    of the avatar block, then emits a partial Child that the caller
    finalizes on ``</a>``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._inside_menu = False
        self._menu_depth = 0
        self._menu_seen = False
        self._inside_a = False
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._capture_buffer: list[str] = []
        self.children: list[Child] = []

    @property
    def found_menu(self) -> bool:
        return self._menu_seen

    # HTMLParser API
    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "el-menu":
            if a.get("id") == _MENU_ID and not self._inside_menu:
                self._inside_menu = True
                self._menu_depth = 1
                self._menu_seen = True
            elif self._inside_menu:
                self._menu_depth += 1
            return
        if not self._inside_menu:
            return
        self._menu_depth += 1
        if tag == "a" and not self._inside_a:
            href = a.get("href", "")
            if not href:
                return
            self._inside_a = True
            self._current = {"href": href, "spans": ""}
            self._capture_buffer = []
            return
        if self._inside_a:
            if tag == "div":
                style = a.get("style")
                if style:
                    m = _COLOR_RE.search(style)
                    if m and self._current is not None:
                        self._current["color"] = m.group(1)
            if tag == "span":
                self._capture = "span"
                self._capture_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if not self._inside_menu:
            return
        self._menu_depth -= 1
        if tag == "el-menu" and self._menu_depth <= 0:
            self._inside_menu = False
            return
        if tag == "a" and self._inside_a:
            self._finalize_a()
            self._inside_a = False
            self._current = None
            return
        if tag == "span" and self._capture == "span":
            text = "".join(self._capture_buffer).strip()
            if self._current is not None:
                spans = self._current.get("spans", "")
                # The first <span> is the avatar initials (inside <div>),
                # the second is the visible name. Capture both in order.
                self._current["spans"] = (spans + "\x1f" + text) if spans else text
            self._capture = None
            self._capture_buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture == "span":
            self._capture_buffer.append(data)

    def _finalize_a(self) -> None:
        if self._current is None:
            return
        href = self._current.get("href", "")
        m = _HREF_RE.match(href)
        if not m:
            return
        spans = self._current.get("spans", "")
        parts = spans.split("\x1f") if spans else []
        # The avatar initials come from the first <span>, the name from
        # the second. In the spike markup they appear in document order.
        initials = parts[0] if len(parts) >= 1 else ""
        first_name = parts[1] if len(parts) >= 2 else ""
        if not first_name:
            # No second span → not a child menu item.
            return
        self.children.append(
            Child(
                child_id=m.group("uuid"),
                first_name=first_name,
                last_name="",  # discovery deferred to announcements spec
                group=None,
                avatar_color=self._current.get("color", ""),
                initials=initials,
            )
        )


def parse_children_list(html: str) -> CliResult[list[Child]]:
    """Parse the ``<el-menu id="menu-0">`` block out of ``html``.

    Empty menu → ``Ok([])``. Missing menu → ``Err(PLATFORM_CHANGED)``.
    """
    parser = _MenuParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed HTML shouldn't crash the CLI
        return Err(
            CliError(
                kind=CliErrorKind.PLATFORM_CHANGED, subject="children_list"
            )
        )
    if not parser.found_menu:
        return Err(
            CliError(
                kind=CliErrorKind.PLATFORM_CHANGED, subject="children_list"
            )
        )
    return Ok(parser.children)


def parse_children_from_response(response: Response) -> CliResult[list[Child]]:
    """Convenience: extract the body text and delegate to ``parse_children_list``."""
    return parse_children_list(response.text())


__all__ = ["parse_children_from_response", "parse_children_list"]
