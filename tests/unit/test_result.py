"""Unit tests for the Result / Ok / Err primitives."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from inso_dumper._result import Err, Ok, Result


def _assign(instance: object, attr: str, value: object) -> None:
    setattr(instance, attr, value)


def test_ok_is_frozen_and_slotted() -> None:
    ok: Ok[int] = Ok(1)
    assert ok.value == 1
    with pytest.raises(FrozenInstanceError):
        _assign(ok, "value", 2)


def test_err_is_frozen_and_slotted() -> None:
    err: Err[str] = Err("boom")
    assert err.error == "boom"
    with pytest.raises(FrozenInstanceError):
        _assign(err, "error", "nope")


def test_match_covers_both_arms() -> None:
    ok: Result[int, str] = Ok(2)
    err: Result[int, str] = Err("e")

    def classify(r: Result[int, str]) -> str:
        match r:
            case Ok(value=v):
                return f"ok:{v}"
            case Err(error=e):
                return f"err:{e}"

    assert classify(ok) == "ok:2"
    assert classify(err) == "err:e"


def test_result_is_a_union_alias() -> None:
    # If this typechecks, the alias resolves correctly.
    ok: Result[int, str] = Ok(1)
    err: Result[int, str] = Err("e")
    assert isinstance(ok, Ok)
    assert isinstance(err, Err)
