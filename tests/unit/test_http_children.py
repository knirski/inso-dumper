"""Unit tests for the pure children-list HTML parser."""

from __future__ import annotations

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.children import parse_children_list

# The exact sidebar markup captured during the discovery spike.
# See docs/api-notes.md "Sidebar markup that exposes children".
SPIKE_MENU = """
<el-menu id="menu-0" role="menu">
  <a href="https://app.inso.pl/panel/home/eea48660-3740-11ed-a611-06dd2728d782" id="item-2" role="menuitem">
    <div style="background-color: #FCCC34;"><span>FK</span></div>
    <span>Franek</span>
  </a>
  <a href="https://app.inso.pl/panel/home/a703dc5a-042d-4122-96e8-4c5111e5f7cf" id="item-3" role="menuitem">
    <div style="background-color: #A9CC63;"><span>Z</span></div>
    <span>Zofia</span>
  </a>
  <a href="/panel/add-child" id="item-4" role="menuitem">
    <div><i class="fal fa-plus"></i></div>
    <span>Dodaj dziecko</span>
  </a>
</el-menu>
"""


def test_parses_two_children_from_spike_markup() -> None:
    result = parse_children_list(SPIKE_MENU)
    assert isinstance(result, Ok)
    children = result.value
    assert len(children) == 2
    assert children[0].first_name == "Franek"
    assert children[0].child_id == "eea48660-3740-11ed-a611-06dd2728d782"
    assert children[0].avatar_color == "#FCCC34"
    assert children[0].initials == "FK"
    assert children[0].slug == "franek"
    assert children[1].first_name == "Zofia"
    assert children[1].child_id == "a703dc5a-042d-4122-96e8-4c5111e5f7cf"
    assert children[1].avatar_color == "#A9CC63"
    assert children[1].initials == "Z"
    assert children[1].slug == "zofia"


def test_add_child_link_is_ignored() -> None:
    """The /panel/add-child href is a menu action, not a child."""
    result = parse_children_list(SPIKE_MENU)
    assert isinstance(result, Ok)
    ids = [c.child_id for c in result.value]
    assert all("add-child" not in cid for cid in ids)
    assert "Dodaj dziecko" not in [c.first_name for c in result.value]


def test_empty_menu_is_ok_empty() -> None:
    """An empty <el-menu> means 'no children on this account' — valid state."""
    html = '<el-menu id="menu-0" role="menu"></el-menu>'
    result = parse_children_list(html)
    assert isinstance(result, Ok)
    assert result.value == []


def test_missing_menu_block_returns_platform_changed() -> None:
    html = "<html><body>no menu here</body></html>"
    result = parse_children_list(html)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.PLATFORM_CHANGED
    assert result.error.subject == "children_list"


def test_non_inso_href_is_ignored() -> None:
    html = """
<el-menu id="menu-0" role="menu">
  <a href="https://app.inso.pl/panel/home/aaaa-bbbb-cccc" id="x">
    <div style="background-color: #000000;"><span>AA</span></div>
    <span>Anna</span>
  </a>
  <a href="https://other.example/foo" id="y">
    <div style="background-color: #111111;"><span>OO</span></div>
    <span>Other</span>
  </a>
</el-menu>
"""
    result = parse_children_list(html)
    assert isinstance(result, Ok)
    assert len(result.value) == 1
    assert result.value[0].first_name == "Anna"


def test_initials_recovered_from_avatar_div() -> None:
    html = """
<el-menu id="menu-0" role="menu">
  <a href="https://app.inso.pl/panel/home/abc-def">
    <div style="background-color: #AABBCC;"><span>XY</span></div>
    <span>Xavier</span>
  </a>
</el-menu>
"""
    result = parse_children_list(html)
    assert isinstance(result, Ok)
    assert result.value[0].initials == "XY"
    assert result.value[0].avatar_color == "#AABBCC"


def test_parser_does_not_import_httpx() -> None:
    """Functional core: no IO imports in this module."""
    import inspect

    from inso_dumper.http import children as mod

    source = inspect.getsource(mod)
    assert "import httpx" not in source
    assert "import requests" not in source
    assert "open(" not in source
