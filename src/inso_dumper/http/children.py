"""Pure HTML → ``list[Child]`` parser for the Inso child switcher.

Functional core: no IO, no ``httpx``, no ``open``. The shell in
``inso_dumper.http.children_shell`` is the only caller; it pulls the
final dashboard ``Response.text()`` and passes the string in.

Markup notes (re-verified against live traffic Aug 2026; the old
``<el-menu id="menu-0">`` wrapper is gone): child entries are ``<a>``
elements whose href is the absolute
``https://app.inso.pl/panel/home/<uuid>`` and which contain an avatar
``<div>`` with a ``background-color`` style plus two ``<span>``s
(initials, then first name). The same entries render twice (desktop and
mobile popovers) and the main nav carries same-URL links without the
avatar/name structure, so entries are deduplicated by ``child_id`` and
only avatar+name anchors count.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from html.parser import HTMLParser

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.children import Child

_HREF_RE = re.compile(
    r"^https://app\.inso\.pl/panel/home/(?P<uuid>[0-9a-f-]+)/?$",
    re.IGNORECASE,
)
_COLOR_RE = re.compile(r"background-color:\s*(#[0-9a-fA-F]{3,8})")


class _SwitcherParser(HTMLParser):
    """Streaming parser that collects child-switcher ``<a>`` entries.

    For each ``<a href="https://app.inso.pl/panel/home/<uuid>">`` it
    captures avatar ``background-color`` styles and ``<span>`` texts in
    document order. An anchor finalizes into a ``Child`` only when it
    has an avatar colour and at least two spans (initials, then name) —
    same-URL nav links ("Podsumowanie") lack both and are ignored.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._anchor_depth = 0
        self._matching = False
        self._uuid: str | None = None
        self._color: str | None = None
        self._spans: list[str] = []
        self._span_open = 0
        self._span_buffer: list[str] = []
        self._anchor_seen = False
        self.children: list[Child] = []
        self._seen_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "a":
            if self._anchor_depth == 0:
                href = a.get("href", "")
                m = _HREF_RE.match(href) if href else None
                if m:
                    self._matching = True
                    self._anchor_seen = True
                    self._uuid = m.group("uuid")
                    self._color = None
                    self._spans = []
            self._anchor_depth += 1
            return
        if not self._matching:
            return
        if tag == "div":
            style = a.get("style")
            if style:
                found = _COLOR_RE.search(style)
                if found:
                    self._color = found.group(1)
        elif tag == "span":
            self._span_open += 1
            self._span_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._span_open > 0:
            self._spans.append("".join(self._span_buffer).strip())
            self._span_open -= 1
            self._span_buffer = []
            return
        if tag != "a" or self._anchor_depth == 0:
            return
        self._anchor_depth -= 1
        if self._anchor_depth == 0 and self._matching:
            self._finalize()
            self._matching = False
            self._uuid = None
            self._color = None
            self._spans = []

    def handle_data(self, data: str) -> None:
        if self._span_open > 0:
            self._span_buffer.append(data)

    def _finalize(self) -> None:
        if self._color is None or self._uuid is None or len(self._spans) < 2:
            return
        initials, first_name = self._spans[0], self._spans[1]
        if not first_name or self._uuid in self._seen_ids:
            return
        self._seen_ids.add(self._uuid)
        self.children.append(
            Child(
                child_id=self._uuid,
                first_name=first_name,
                last_name="",  # not present in the switcher; see api-notes.md
                group=None,
                avatar_color=self._color,
                initials=initials,
            )
        )


def parse_children_list(html: str) -> CliResult[list[Child]]:
    """Parse the child-switcher anchors out of the dashboard ``html``.

    No child-switcher anchor at all → ``Err(PLATFORM_CHANGED)`` (the
    markup drifted again). Anchors that match the URL but lack the
    avatar/name structure are ignored; an account whose anchors carry no
    usable name is ``Ok([])``.
    """
    parser = _SwitcherParser()
    # stdlib's HTMLParser does not raise on malformed markup — it
    # silently records errors and continues. Programmer errors (bugs
    # in this module) propagate to the CLI's last-resort safety net
    # as INTERNAL, which is the correct classification.
    parser.feed(html)
    parser.close()
    if not parser._anchor_seen:
        return Err(CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="children_list"))
    return Ok(parser.children)


__all__ = ["parse_children_list"]
