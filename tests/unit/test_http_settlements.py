"""Unit tests for the settlements history-page parser
(http/settlements.py)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind
from inso_dumper.http.settlements import parse_settlements_history
from inso_dumper.models.settlements import SettlementStatus

FIXTURE = Path(__file__).parent / "fixtures" / "settlements-history-small.html"
CORPUS = Path("docs/api-notes-data/billing-history-page.html")


def _corpus_html() -> str:
    """The spike capture is saved as a JSON-quoted string (HAR-style);
    unescape when needed."""
    if not CORPUS.exists():
        pytest.skip(f"spike corpus absent: {CORPUS}")
    raw = CORPUS.read_text(encoding="utf-8")
    if raw.startswith('"'):
        return str(json.loads(raw))
    return raw


def _parse_fixture() -> object:
    return parse_settlements_history(FIXTURE.read_text(encoding="utf-8"))


# --- synthetic fixture -------------------------------------------------------


def test_fixture_parses_two_months() -> None:
    match _parse_fixture():
        case Ok(page):
            assert [m.month for m in page.months] == ["2026-08", "2026-07"]
        case other:
            pytest.fail(f"expected Ok, got {other}")


def test_fixture_first_card_full_shape() -> None:
    match _parse_fixture():
        case Ok(page):
            month = page.months[0]
        case other:
            pytest.fail(f"expected Ok, got {other}")
    assert month.amount_pln == Decimal("1424.00")
    assert month.saldo_pln == Decimal("0.00")
    assert month.due_date == date(2026, 8, 11)
    assert month.status is SettlementStatus.PAID
    assert month.invoice_bill_uuid == "0b5b2e76-6473-4166-8fed-d864e8845a81"
    by_label = {item.label: item for item in month.items}
    assert by_label["Czesne"].amount_pln == Decimal("1080.00")
    assert by_label["Czesne"].is_summary is False
    assert by_label["Śniadanie"].count == 16
    assert by_label["Śniadanie"].amount_pln == Decimal("64.00")
    assert by_label["Obiad"].count == 22
    assert by_label["Suma"].is_summary is True
    assert by_label["Suma"].amount_pln == Decimal("344.00")
    assert by_label["Do zapłaty"].is_summary is True
    assert by_label["Do zapłaty"].amount_pln == Decimal("1424.00")
    assert by_label["Aktualne saldo"].is_summary is True
    assert by_label["Aktualne saldo"].amount_pln == Decimal("0.00")


def test_fixture_second_card_has_no_invoice_and_no_items() -> None:
    match _parse_fixture():
        case Ok(page):
            month = page.months[1]
        case other:
            pytest.fail(f"expected Ok, got {other}")
    assert month.invoice_bill_uuid is None
    assert month.items == []
    assert month.amount_pln == Decimal("1080.00")
    assert month.due_date == date(2026, 7, 11)


# --- drift -------------------------------------------------------------------


def test_duplicate_month_key_drifts() -> None:
    html = (
        '<div class="text-lg font-semibold pl-2 mb-2 mt-6">Sierpień 2026</div>'
        '<div class="bg-white shadow overflow-hidden rounded-lg mb-6 shadow">'
        '<div class="grid grid-cols-7"><div class="col-span-4">'
        "Kwota 1 424,00 zł</div>"
        '<div class="col-span-3">Saldo 0,00 zł</div>'
        '<div class="col-span-7">Termin 11 sierpnia 2026</div></div></div>'
        '<div class="text-lg font-semibold pl-2 mb-2 mt-6">sierpnia 2026</div>'
        '<div class="bg-white shadow overflow-hidden rounded-lg mb-6 shadow">'
        '<div class="grid grid-cols-7"><div class="col-span-4">'
        "Kwota 1 424,00 zł</div>"
        '<div class="col-span-3">Saldo 0,00 zł</div>'
        '<div class="col-span-7">Termin 11 sierpnia 2026</div></div></div>'
    )
    match parse_settlements_history(html):
        case Err(error):
            assert error.kind is CliErrorKind.PLATFORM_CHANGED
            assert error.subject == "settlements_duplicate_month"
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_card_missing_kwota_drifts() -> None:
    html = (
        '<div class="text-lg font-semibold pl-2 mb-2 mt-6">Sierpień 2026</div>'
        '<div class="bg-white shadow overflow-hidden rounded-lg mb-6 shadow">'
        '<div class="grid grid-cols-7"><div class="col-span-3">'
        "Saldo 0,00 zł</div></div></div>"
    )
    match parse_settlements_history(html):
        case Err(error):
            assert error.kind is CliErrorKind.PLATFORM_CHANGED
            assert error.subject == "settlements_month_card"
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_card_without_label_drifts() -> None:
    html = (
        '<div class="bg-white shadow overflow-hidden rounded-lg mb-6 shadow">'
        '<div class="grid grid-cols-7"><div class="col-span-4">'
        "Kwota 1 424,00 zł</div>"
        '<div class="col-span-3">Saldo 0,00 zł</div>'
        '<div class="col-span-7">Termin 11 sierpnia 2026</div></div></div>'
    )
    match parse_settlements_history(html):
        case Err(error):
            assert error.subject == "settlements_month_card"
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_garbage_row_amount_drifts_money() -> None:
    html = (
        '<div class="text-lg font-semibold pl-2 mb-2 mt-6">Sierpień 2026</div>'
        '<div class="bg-white shadow overflow-hidden rounded-lg mb-6 shadow">'
        "<table><tbody><tr><td>Czesne</td><td>dużo zł</td></tr></tbody></table>"
        '<div class="grid grid-cols-7"><div class="col-span-4">'
        "Kwota 1 424,00 zł</div>"
        '<div class="col-span-3">Saldo 0,00 zł</div>'
        '<div class="col-span-7">Termin 11 sierpnia 2026</div></div></div>'
    )
    match parse_settlements_history(html):
        case Err(error):
            assert error.subject == "settlements_money"
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_unrecognized_badge_maps_to_unknown_status() -> None:
    html = (
        '<div class="text-lg font-semibold pl-2 mb-2 mt-6">Sierpień 2026</div>'
        '<div class="bg-white shadow overflow-hidden rounded-lg mb-6 shadow">'
        '<span class="bg-red-100 text-red-700">zaległe</span>'
        '<div class="grid grid-cols-7"><div class="col-span-4">'
        "Kwota 1 424,00 zł</div>"
        '<div class="col-span-3">Saldo 0,00 zł</div>'
        '<div class="col-span-7">Termin 11 sierpnia 2026</div></div></div>'
    )
    match parse_settlements_history(html):
        case Ok(page):
            assert page.months[0].status is SettlementStatus.UNKNOWN
        case other:
            pytest.fail(f"expected Ok, got {other}")


def test_empty_page_yields_no_months() -> None:
    assert parse_settlements_history("<html><body></body></html>") == Ok(_page_months_empty())


def _page_months_empty() -> object:
    from inso_dumper.models.settlements import SettlementHistoryPage

    return SettlementHistoryPage()


# --- spike corpus (skip when absent) -----------------------------------------


def test_corpus_parses_47_months() -> None:
    if not CORPUS.exists():
        pytest.skip(f"spike corpus absent: {CORPUS}")
    match parse_settlements_history(_corpus_html()):
        case Ok(page):
            months = page.months
        case other:
            pytest.fail(f"expected Ok, got {other}")
    assert len(months) == 47
    # The page renders newest-first.
    assert [m.month for m in months] == sorted((m.month for m in months), reverse=True)


def test_corpus_2026_08_card_matches_capture() -> None:
    if not CORPUS.exists():
        pytest.skip(f"spike corpus absent: {CORPUS}")
    match parse_settlements_history(_corpus_html()):
        case Ok(page):
            month = page.months[0]
        case other:
            pytest.fail(f"expected Ok, got {other}")
    assert month.month == "2026-08"
    assert month.amount_pln == Decimal("1424.00")
    assert month.saldo_pln == Decimal("0.00")
    assert month.due_date == date(2026, 8, 11)
    assert month.status is SettlementStatus.PAID
    assert month.invoice_bill_uuid == "a907a688-322b-4044-bb52-09ecd986a769"
    by_label = {item.label: item for item in month.items}
    assert by_label["Czesne"].amount_pln == Decimal("1080.00")
    assert by_label["Śniadanie"].count == 16
    assert by_label["Suma"].is_summary is True
    assert by_label["Do zapłaty"].amount_pln == Decimal("1424.00")
    assert by_label["Zapłacono"].amount_pln == Decimal("1424.00")
    assert by_label["Aktualne saldo"].amount_pln == Decimal("0.00")


# --- fetch shells (T3) -------------------------------------------------------

import asyncio  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402

from inso_dumper.config import Config  # noqa: E402
from inso_dumper.errors import CliResult  # noqa: E402
from inso_dumper.http.settlements import (  # noqa: E402
    fetch_invoice,
    fetch_settlements_history,
)
from inso_dumper.models.settlements import SettlementHistoryPage  # noqa: E402
from tests._fakes import FakeHttpClient, make_session  # noqa: E402

CHILD_UUID = "eea48660-3740-11ed-a611-06dd2728d782"
BILL_UUID = "0b5b2e76-6473-4166-8fed-d864e8845a81"
HISTORY_PATH = f"/panel/child/{CHILD_UUID}/settlement"
INVOICE_PATH = f"/panel/settlement/{BILL_UUID}/invoice"


def _collect(
    ait: AsyncIterator[CliResult[SettlementHistoryPage]],
) -> list[CliResult[SettlementHistoryPage]]:
    async def _run() -> list[CliResult[SettlementHistoryPage]]:
        items: list[CliResult[SettlementHistoryPage]] = []
        async for item in ait:
            items.append(item)
        return items

    return asyncio.run(_run())


def test_fetch_history_200_parses_page() -> None:
    client = FakeHttpClient([(200, _corpus_html().encode("utf-8"), [])])
    cfg = Config()
    items = _collect(fetch_settlements_history(client, cfg, make_session(), CHILD_UUID))
    assert len(items) == 1
    match items[0]:
        case Ok(page):
            assert len(page.months) == 47
        case other:
            pytest.fail(f"expected Ok, got {other}")
    assert client.calls[0]["path"] == HISTORY_PATH


def test_fetch_history_404_is_http_error() -> None:
    client = FakeHttpClient([(404, b"", [])])
    cfg = Config()
    items = _collect(fetch_settlements_history(client, cfg, make_session(), CHILD_UUID))
    match items[0]:
        case Err(error):
            assert error.kind is CliErrorKind.HTTP
            assert error.subject == "settlements_status_404"
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_fetch_history_login_redirect_is_session_expired() -> None:
    client = FakeHttpClient([(302, b"", [("Location", "/login")])])
    cfg = Config()
    items = _collect(fetch_settlements_history(client, cfg, make_session(), CHILD_UUID))
    match items[0]:
        case Err(error):
            assert error.kind is CliErrorKind.SESSION_EXPIRED
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_fetch_history_follows_same_origin_redirect() -> None:
    client = FakeHttpClient(
        [
            (301, b"", [("Location", f"{HISTORY_PATH}/")]),
            (200, _corpus_html().encode("utf-8"), []),
        ]
    )
    cfg = Config()
    items = _collect(fetch_settlements_history(client, cfg, make_session(), CHILD_UUID))
    match items[0]:
        case Ok(page):
            assert len(page.months) == 47
        case other:
            pytest.fail(f"expected Ok, got {other}")
    assert client.calls[1]["path"] == f"{HISTORY_PATH}/"


def test_fetch_history_transport_error_propagates() -> None:
    client = FakeHttpClient([Err(CliError(kind=CliErrorKind.HTTP, subject="timeout"))])
    cfg = Config()
    items = _collect(fetch_settlements_history(client, cfg, make_session(), CHILD_UUID))
    match items[0]:
        case Err(error):
            assert error.subject == "timeout"
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_fetch_invoice_200_pdf_returns_bytes() -> None:
    body = b"%PDF-1.4 fake-bytes"
    client = FakeHttpClient([(200, body, [("Content-Type", "application/pdf")])])
    cfg = Config()
    result = asyncio.run(fetch_invoice(client, cfg, make_session(), BILL_UUID))
    match result:
        case Ok(data):
            assert data == body
        case other:
            pytest.fail(f"expected Ok, got {other}")
    assert client.calls[0]["path"] == INVOICE_PATH


def test_fetch_invoice_404_is_http_error() -> None:
    client = FakeHttpClient([(404, b"", [])])
    cfg = Config()
    result = asyncio.run(fetch_invoice(client, cfg, make_session(), BILL_UUID))
    match result:
        case Err(error):
            assert error.kind is CliErrorKind.HTTP
            assert error.subject == "invoice_status_404"
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_fetch_invoice_non_pdf_is_drift() -> None:
    client = FakeHttpClient(
        [(200, b"<html>login</html>", [("Content-Type", "text/html; charset=utf-8")])]
    )
    cfg = Config()
    result = asyncio.run(fetch_invoice(client, cfg, make_session(), BILL_UUID))
    match result:
        case Err(error):
            assert error.kind is CliErrorKind.PLATFORM_CHANGED
            assert error.subject == "invoice_content_type"
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_fetch_invoice_login_redirect_is_session_expired() -> None:
    client = FakeHttpClient([(302, b"", [("Location", "/login")])])
    cfg = Config()
    result = asyncio.run(fetch_invoice(client, cfg, make_session(), BILL_UUID))
    match result:
        case Err(error):
            assert error.kind is CliErrorKind.SESSION_EXPIRED
        case other:
            pytest.fail(f"expected Err, got {other}")


def test_settlements_requests_are_get_only() -> None:
    client = FakeHttpClient(
        [
            (200, _corpus_html().encode("utf-8"), []),
            (200, b"%PDF", [("Content-Type", "application/pdf")]),
        ]
    )
    cfg = Config()
    _collect(fetch_settlements_history(client, cfg, make_session(), CHILD_UUID))
    asyncio.run(fetch_invoice(client, cfg, make_session(), BILL_UUID))
    assert all(call["method"] == "GET" for call in client.calls)
