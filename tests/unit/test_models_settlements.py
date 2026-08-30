"""Unit tests for the settlement domain models and pure normalizers
(models/settlements.py)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliErrorKind
from inso_dumper.models.settlements import (
    SettlementHistoryPage,
    SettlementItem,
    SettlementMonth,
    SettlementStatus,
    parse_due_date,
    parse_money,
    parse_month_key,
)

# --- parse_money ------------------------------------------------------------


def testparse_money_thousands_space_and_comma() -> None:
    assert parse_money("1 424,00 zł") == Ok(Decimal("1424.00"))


def testparse_money_nbsp_thousands() -> None:
    assert parse_money("1\u00a0424,00 zł") == Ok(Decimal("1424.00"))


def testparse_money_negative() -> None:
    assert parse_money("-50,50 zł") == Ok(Decimal("-50.50"))


def testparse_money_plain() -> None:
    assert parse_money("0,00 zł") == Ok(Decimal("0.00"))


@pytest.mark.parametrize(
    "garbage",
    ["", "zł", "dużo zł", "12,34,56 zł", "1 424 złoty"],
)
def testparse_money_garbage_drifts(garbage: str) -> None:
    match parse_money(garbage):
        case Err(error):
            assert error.kind is CliErrorKind.PLATFORM_CHANGED
            assert error.subject == "settlements_money"
        case other:
            pytest.fail(f"expected Err, got {other}")


# --- parse_month_key --------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Styczeń 2023", "2023-01"),
        ("Luty 2023", "2023-02"),
        ("Marzec 2023", "2023-03"),
        ("Kwiecień 2023", "2023-04"),
        ("Maj 2023", "2023-05"),
        ("Czerwiec 2023", "2023-06"),
        ("Lipiec 2023", "2023-07"),
        ("Sierpień 2026", "2026-08"),
        ("Wrzesień 2022", "2022-09"),
        ("Październik 2022", "2022-10"),
        ("Listopad 2022", "2022-11"),
        ("Grudzień 2022", "2022-12"),
        # Genitive forms (as in due dates) resolve too.
        ("sierpnia 2026", "2026-08"),
        ("11 czerwca 2023", "2023-06"),
    ],
)
def testparse_month_key_twelve_months(label: str, expected: str) -> None:
    assert parse_month_key(label) == Ok(expected)


@pytest.mark.parametrize("garbage", ["", "2023-06", "Smok 2023", "Czerwiec"])
def testparse_month_key_garbage_drifts(garbage: str) -> None:
    match parse_month_key(garbage):
        case Err(error):
            assert error.kind is CliErrorKind.PLATFORM_CHANGED
            assert error.subject == "settlements_month_name"
        case other:
            pytest.fail(f"expected Err, got {other}")


# --- parse_due_date ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11 sierpnia 2026", date(2026, 8, 11)),
        ("1 marca 2024", date(2024, 3, 1)),
        ("31 grudnia 2022", date(2022, 12, 31)),
    ],
)
def testparse_due_date_polish_months(text: str, expected: date) -> None:
    assert parse_due_date(text) == Ok(expected)


@pytest.mark.parametrize("garbage", ["", "jutro", "32 sierpnia 2026", "11 smoka 2026"])
def testparse_due_date_garbage_drifts(garbage: str) -> None:
    match parse_due_date(garbage):
        case Err(error):
            assert error.kind is CliErrorKind.PLATFORM_CHANGED
            assert error.subject == "settlements_month_name"
        case other:
            pytest.fail(f"expected Err, got {other}")


# --- models ------------------------------------------------------------------


def _item_dict(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "label": "Czesne",
        "amount_pln": "700.00",
        "count": None,
        "is_summary": False,
    }
    item.update(overrides)
    return item


def _month_dict(**overrides: object) -> dict[str, object]:
    month: dict[str, object] = {
        "month": "2026-08",
        "amount_pln": "1424.00",
        "saldo_pln": "0.00",
        "due_date": "2026-08-11",
        "status": "paid",
        "items": [_item_dict()],
        "invoice_bill_uuid": "abc-123",
    }
    month.update(overrides)
    return month


def test_settlement_month_validates_captured_shape() -> None:
    month = SettlementMonth.model_validate(_month_dict())
    assert month.month == "2026-08"
    assert month.amount_pln == Decimal("1424.00")
    assert month.saldo_pln == Decimal("0.00")
    assert month.due_date == date(2026, 8, 11)
    assert month.status is SettlementStatus.PAID
    assert month.items[0].label == "Czesne"
    assert month.invoice_bill_uuid == "abc-123"


def test_settlement_month_defaults() -> None:
    month = SettlementMonth.model_validate(
        {
            "month": "2026-08",
            "amount_pln": "1424.00",
            "saldo_pln": "0.00",
            "due_date": None,
            "status": "unknown",
        }
    )
    assert month.items == []
    assert month.invoice_bill_uuid is None
    assert month.status is SettlementStatus.UNKNOWN


def test_settlement_month_rejects_bad_month_key() -> None:
    with pytest.raises(ValidationError):
        _ = SettlementMonth.model_validate(_month_dict(month="August 2026"))


def test_settlement_month_unknown_field_raises() -> None:
    with pytest.raises(ValidationError):
        _ = SettlementMonth.model_validate(_month_dict(brandNewKey=1))


def test_settlement_item_unknown_field_raises() -> None:
    with pytest.raises(ValidationError):
        _ = SettlementItem.model_validate(_item_dict(brandNewKey=1))


def test_settlement_history_page_defaults_and_roundtrip() -> None:
    page = SettlementHistoryPage.model_validate({"months": [_month_dict()]})
    assert len(page.months) == 1
    assert SettlementHistoryPage().months == []
