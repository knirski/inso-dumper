"""Unit tests for the pure children-list HTML parser.

Fixtures mirror the live dashboard markup (re-verified Aug 2026):
child entries are absolute ``panel/home/<uuid>`` anchors with an avatar
div and two spans, rendered once per popover (desktop + mobile) — hence
the duplicates below. See docs/api-notes.md "Sidebar markup that
exposes children".
"""

from __future__ import annotations

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.children import parse_children_list

UUID_1 = "eea48660-3740-11ed-a611-06dd2728d782"
UUID_2 = "a703dc5a-042d-4122-96e8-4c5111e5f7cf"

SWITCHER_ENTRY = (
    '<a href="https://app.inso.pl/panel/home/{uuid}"'
    ' class="flex items-center pl-2 rounded-[10px] px-3 py-2.5 no-underline'
    " font-normal text-sm text-gray-700 transition-colors hover:bg-blue-100"
    ' focus:bg-blue-100 focus:outline-none">\n'
    '    <div class="inline-flex items-center justify-center h-8 w-8'
    ' rounded-full" style="background-color: {color};"><span'
    ' class="font-bold text-xs leading-none uppercase" style="color:'
    ' #639302;">{initials}</span></div>\n'
    '    <div class="ml-1 inline-flex flex-col justify-center items-start'
    ' flex-grow">\n'
    "        <span>{name}</span>\n"
    "    </div>\n"
    "</a>"
)


def _dashboard(with_second_child: bool = True) -> str:
    """A trimmed dashboard: one nav link for the active child (no avatar
    name structure), the switcher entry, the duplicate popover copy, and
    the add-child action."""
    nav = (
        f'<a href="/panel/home/{UUID_1}"'
        ' class="flex justify-between items-center h-[52px] w-full my-1'
        ' px-1 text-white rounded-lg hover:bg-blue-500 bg-blue-500">\n'
        '    <div class="flex justify-center items-center">\n'
        '        <i class="fal fa-home text-center text-xl w-12 font-semibold"></i>\n'
        '        <span class="font-semibold">Podsumowanie</span>\n'
        "    </div>\n"
        "</a>"
    )
    add_child = (
        '<a href="/panel/add-child" class="flex items-center pl-2'
        " rounded-[10px] px-3 py-2.5 no-underline font-normal text-sm"
        " text-gray-700 transition-colors hover:bg-blue-100"
        ' focus:bg-blue-100 focus:outline-none">\n'
        '    <div class="inline-flex items-center justify-center h-8 w-8'
        ' bg-neutral-400 rounded-full"><i class="fal fa-plus text-neutral-100'
        ' text-sm"></i></div>\n'
        '    <span class="ml-1">Dodaj dziecko</span>\n'
        "</a>"
    )
    first = SWITCHER_ENTRY.format(uuid=UUID_1, color="#FCCC34", initials="FK", name="Franek")
    second = (
        SWITCHER_ENTRY.format(uuid=UUID_2, color="#A9CC63", initials="LI", name="Zofia")
        if with_second_child
        else ""
    )
    return f"<html><body>{nav}{first}{second}{add_child}{first}</body></html>"


def test_parses_children_from_live_markup() -> None:
    result = parse_children_list(_dashboard())

    assert isinstance(result, Ok)
    children = result.value
    assert len(children) == 2
    assert children[0].first_name == "Franek"
    assert children[0].child_id == UUID_1
    assert children[0].avatar_color == "#FCCC34"
    assert children[0].initials == "FK"
    assert children[0].slug == "franek"
    assert children[1].first_name == "Zofia"
    assert children[1].child_id == UUID_2
    assert children[1].initials == "LI"
    assert children[1].slug == "zofia"


def test_duplicate_popover_entries_deduplicate_by_uuid() -> None:
    """The switcher renders twice (desktop + mobile popovers); the
    second copy must not yield a duplicate Child."""
    result = parse_children_list(_dashboard())
    assert isinstance(result, Ok)
    ids = [c.child_id for c in result.value]
    assert len(ids) == len(set(ids))


def test_nav_and_action_links_are_not_children() -> None:
    """Same-URL nav links ("Podsumowanie") and /panel/add-child lack the
    avatar+name structure — they must not become Child entries."""
    result = parse_children_list(_dashboard())
    assert isinstance(result, Ok)
    assert "Podsumowanie" not in [c.first_name for c in result.value]
    assert "Dodaj dziecko" not in [c.first_name for c in result.value]


def test_single_child_account() -> None:
    result = parse_children_list(_dashboard(with_second_child=False))
    assert isinstance(result, Ok)
    assert [c.first_name for c in result.value] == ["Franek"]


def test_no_switcher_anchor_returns_platform_changed() -> None:
    """No child-switcher anchor at all means the markup drifted again —
    fail loud instead of returning a silently empty list."""
    html = "<html><body><p>unexpected page</p></body></html>"
    result = parse_children_list(html)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
    assert result.error.subject == "children_list"


def test_anchor_without_avatar_or_name_is_ignored() -> None:
    """A panel-home anchor that lacks the avatar/name structure (e.g. a
    bare nav link) is not a child; with no other anchors the parse is an
    empty success, not drift."""
    html = f'<a href="https://app.inso.pl/panel/home/{UUID_1}"><span>Podsumowanie</span></a>'
    result = parse_children_list(html)
    assert isinstance(result, Ok)
    assert result.value == []


def test_relative_href_is_ignored() -> None:
    html = f'<a href="/panel/home/{UUID_1}"><div style="background-color: #000;"></div><span>A</span><span>Anna</span></a>'
    result = parse_children_list(html)
    assert isinstance(result, Err)  # no absolute switcher anchor at all


def test_parser_does_not_import_httpx() -> None:
    """Functional core: no IO imports in this module."""
    import inspect

    from inso_dumper.http import children as mod

    source = inspect.getsource(mod)
    assert "import httpx" not in source
    assert "import requests" not in source
    assert "open(" not in source
