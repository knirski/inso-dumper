"""Unit tests for the settlements month writer (dump/settlements.py)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.dump.settlements import write_invoice, write_settlement_month
from inso_dumper.errors import CliErrorKind
from inso_dumper.models.settlements import SettlementItem, SettlementMonth


def _month(**overrides: object) -> SettlementMonth:
    fields: dict[str, object] = {
        "month": "2026-08",
        "amount_pln": Decimal("1424.00"),
        "saldo_pln": Decimal("0.00"),
        "due_date": date(2026, 8, 11),
        "status": "paid",
        "items": [
            SettlementItem(label="Czesne", amount_pln=Decimal("1080.00")),
            SettlementItem(label="Śniadanie", amount_pln=Decimal("64.00"), count=16),
            SettlementItem(label="Do zapłaty", amount_pln=Decimal("1424.00"), is_summary=True),
        ],
        "invoice_bill_uuid": "a907a688-322b-4044-bb52-09ecd986a769",
    }
    fields.update(overrides)
    return SettlementMonth.model_validate(fields)


def test_write_settlement_month_matches_design_shape(tmp_path: Path) -> None:
    month_dir = tmp_path / "settlements" / "2026-08"
    result = write_settlement_month(_month(), month_dir)

    assert isinstance(result, Ok)
    assert result.value == month_dir
    data = json.loads((month_dir / "settlement.json").read_text(encoding="utf-8"))
    assert data == {
        "month": "2026-08",
        "status": "paid",
        "amount_pln": "1424.00",
        "saldo_pln": "0.00",
        "due_date": "2026-08-11",
        "items": [
            {
                "label": "Czesne",
                "amount_pln": "1080.00",
                "count": None,
                "is_summary": False,
            },
            {
                "label": "Śniadanie",
                "amount_pln": "64.00",
                "count": 16,
                "is_summary": False,
            },
            {
                "label": "Do zapłaty",
                "amount_pln": "1424.00",
                "count": None,
                "is_summary": True,
            },
        ],
        "invoice_bill_uuid": "a907a688-322b-4044-bb52-09ecd986a769",
    }


def test_write_settlement_month_omits_null_invoice_ref_and_due_date(
    tmp_path: Path,
) -> None:
    month_dir = tmp_path / "2026-07"
    result = write_settlement_month(
        _month(
            month="2026-07",
            due_date=None,
            items=[],
            invoice_bill_uuid=None,
        ),
        month_dir,
    )

    assert isinstance(result, Ok)
    data = json.loads((month_dir / "settlement.json").read_text(encoding="utf-8"))
    assert "invoice_bill_uuid" not in data
    assert "due_date" not in data
    assert data["items"] == []


def test_write_settlement_month_file_and_dir_modes(tmp_path: Path) -> None:
    month_dir = tmp_path / "2026-08"
    result = write_settlement_month(_month(), month_dir)

    assert isinstance(result, Ok)
    assert month_dir.stat().st_mode & 0o777 == 0o700
    assert (month_dir / "settlement.json").stat().st_mode & 0o777 == 0o600


def test_write_settlement_month_overwrite_is_idempotent(tmp_path: Path) -> None:
    month_dir = tmp_path / "2026-08"
    first = write_settlement_month(_month(), month_dir)
    assert isinstance(first, Ok)
    payload = (month_dir / "settlement.json").read_bytes()
    second = write_settlement_month(_month(), month_dir)
    assert isinstance(second, Ok)
    assert (month_dir / "settlement.json").read_bytes() == payload
    assert not list(month_dir.glob("*.tmp"))


def test_write_settlement_month_oserror_is_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inso_dumper.dump.settlements as settlements_mod

    def _boom(path: Path, data: bytes, mode: int = 0o600) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(settlements_mod, "_write_bytes", _boom)
    result = write_settlement_month(_month(), tmp_path / "2026-08")
    match result:
        case Err(error):
            assert error.kind is CliErrorKind.INTERNAL
            assert error.subject == "settlements_write"
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_write_invoice_writes_bytes_atomically(tmp_path: Path) -> None:
    month_dir = tmp_path / "2026-08"
    payload = b"%PDF-1.4 fake"
    result = write_invoice(payload, month_dir)

    assert isinstance(result, Ok)
    assert (month_dir / "invoice.pdf").read_bytes() == payload
    assert month_dir.stat().st_mode & 0o777 == 0o700
    assert (month_dir / "invoice.pdf").stat().st_mode & 0o777 == 0o600


def test_write_invoice_overwrite_is_idempotent(tmp_path: Path) -> None:
    month_dir = tmp_path / "2026-08"
    assert isinstance(write_invoice(b"%PDF-one", month_dir), Ok)
    assert isinstance(write_invoice(b"%PDF-two", month_dir), Ok)
    assert (month_dir / "invoice.pdf").read_bytes() == b"%PDF-two"
    assert not list(month_dir.glob("*.tmp"))


def test_write_invoice_oserror_is_internal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import inso_dumper.dump.settlements as settlements_mod

    def _boom(path: Path, data: bytes, mode: int = 0o600) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(settlements_mod, "_write_bytes", _boom)
    result = write_invoice(b"%PDF", tmp_path / "2026-08")
    match result:
        case Err(error):
            assert error.subject == "settlements_write"
        case other:
            pytest.fail(f"expected Err, got {other}")
