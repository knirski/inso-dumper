"""Unit tests for the Child model and slug derivation."""

from __future__ import annotations

import pytest

from inso_dumper.models.children import Child


def _child(
    *,
    first: str = "Ignacy",
    last: str = "",
    color: str = "#FCCC34",
    initials: str = "IG",
    cid: str = "eea48660-3740-11ed-a611-06dd2728d782",
    group: str | None = None,
) -> Child:
    return Child(
        child_id=cid,
        first_name=first,
        last_name=last,
        group=group,
        avatar_color=color,
        initials=initials,
    )


def test_slug_is_firstname_when_no_last_name() -> None:
    c = _child(first="Ignacy", last="")
    assert c.slug == "ignacy"


def test_slug_includes_last_initial_when_known() -> None:
    c = _child(first="Ignacy", last="Nirski")
    assert c.slug == "ignacy-n"


def test_slug_falls_back_to_child_when_empty() -> None:
    c = _child(first="", last="")
    assert c.slug == "child"


def test_slug_folds_polish_letters_ascii() -> None:
    """The base ASCII-fold collapses Polish diacritics to ASCII."""
    c = _child(first="Łucja", last="")
    # NFKD strip: Ł -> L, ł -> l. After lower: "lucja".
    assert c.slug == "lucja"


def test_slug_collapses_hyphens() -> None:
    c = _child(first="  Anna  Maria ", last="")
    # Hyphens/whitespace collapse via the slug regex.
    slug = c.slug
    assert "--" not in slug
    assert slug.strip("-") == slug


def test_display_name_with_full_name() -> None:
    c = _child(first="Ignacy", last="Nirski")
    assert c.display_name == "Ignacy Nirski"


def test_display_name_first_only_when_no_last() -> None:
    c = _child(first="Ignacy", last="")
    assert c.display_name == "Ignacy"


def test_child_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    c = _child()
    # Indirect setattr bypasses basedpyright's static check on the
    # attribute. The frozen dataclass raises at runtime.
    target: object = c
    with pytest.raises(FrozenInstanceError):
        setattr(target, "first_name", "Other")  # noqa: B010


@pytest.mark.parametrize(
    ("first", "last", "expected"),
    [
        ("Ignacy", "", "ignacy"),
        ("Liliana", "", "liliana"),
        ("Anna", "Kowalska", "anna-k"),
        ("Łucja", "Nowak", "lucja-n"),
        ("", "", "child"),
    ],
)
def test_slug_table(first: str, last: str, expected: str) -> None:
    c = _child(first=first, last=last)
    assert c.slug == expected
