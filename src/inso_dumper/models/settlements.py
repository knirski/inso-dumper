"""Settlement domain models: SettlementStatus, SettlementItem,
SettlementMonth, SettlementHistoryPage — plus the pure Polish-locale
normalizers used by the history-page parser.

Functional core: no IO, no httpx. Models use ``extra="forbid"`` so an
unexpected field fails loud (the parser turns ``ValidationError`` into
``Err(PLATFORM_CHANGED)``) instead of silently dropping data.

Capture reference: docs/api-notes-data/billing-history-page.html
(documented in api-notes.md § Settlements). Observed text shapes:

- money:  ``1 424,00 zł`` (space/NBSP thousands, comma decimals)
- months: ``Czerwiec 2026`` (nominative card label)
- due:    ``11 sierpnia 2026`` (genitive date line)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from inso_dumper._result import Err, Ok, Result
from inso_dumper.errors import CliError, CliErrorKind

# Both nominative (card label: "Czerwiec") and genitive (date line:
# "czerwca") forms, lowercased. "maj" is identical in both.
_POLISH_MONTHS: dict[str, int] = {
    "styczeń": 1,
    "stycznia": 1,
    "luty": 2,
    "lutego": 2,
    "marzec": 3,
    "marca": 3,
    "kwiecień": 4,
    "kwietnia": 4,
    "maj": 5,
    "maja": 5,
    "czerwiec": 6,
    "czerwca": 6,
    "lipiec": 7,
    "lipca": 7,
    "sierpień": 8,
    "sierpnia": 8,
    "wrzesień": 9,
    "września": 9,
    "październik": 10,
    "października": 10,
    "listopad": 11,
    "listopada": 11,
    "grudzień": 12,
    "grudnia": 12,
}


def _money_drift() -> Err[CliError]:
    return Err(CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="settlements_money"))


def _month_name_drift() -> Err[CliError]:
    return Err(CliError(kind=CliErrorKind.PLATFORM_CHANGED, subject="settlements_month_name"))


def parse_money(text: str) -> Result[Decimal, CliError]:
    """Parse ``1 424,00 zł`` into a ``Decimal``.

    Accepts regular spaces or NBSP as thousands separators, a comma
    decimal mark, and an optional leading ``-``. Anything else is
    platform drift.
    """
    cleaned = (
        text.strip()
        .removesuffix("zł")
        .strip()
        .replace("\u00a0", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return _money_drift()
    if not value.is_finite():
        return _money_drift()
    return Ok(value)


def parse_month_key(text: str) -> Result[str, CliError]:
    """Normalize a Polish month label to a ``YYYY-MM`` key.

    Accepts the nominative card label (``Czerwiec 2023``), the genitive
    date form (``11 czerwca 2023``), and a bare genitive month
    (``sierpnia 2026``): the last whitespace-separated token must be the
    year and the token before it a known Polish month name.
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        return _month_name_drift()
    name, year = tokens[-2].lower(), tokens[-1]
    month = _POLISH_MONTHS.get(name)
    if month is None or not year.isdigit() or len(year) != 4:
        return _month_name_drift()
    return Ok(f"{year}-{month:02d}")


def parse_due_date(text: str) -> Result[date, CliError]:
    """Parse ``11 sierpnia 2026`` into a ``date``."""
    tokens = text.strip().split()
    if len(tokens) != 3:
        return _month_name_drift()
    day, name, year = tokens
    month = _POLISH_MONTHS.get(name.lower())
    if month is None or not day.isdigit() or not year.isdigit():
        return _month_name_drift()
    try:
        return Ok(date(int(year), month, int(day)))
    except ValueError:
        # e.g. day 32 or a non-leap Feb 29 — real drift, not programmer error.
        return _month_name_drift()


class SettlementStatus(StrEnum):
    """Closed vocabulary over the observed badge states. Only the paid
    badge was capturable (the account has no outstanding bills); any
    other badge lands here as ``UNKNOWN`` so a future capture can pin
    it without old dumps lying."""

    PAID = "paid"
    UNKNOWN = "unknown"


class SettlementItem(BaseModel):
    """One row of the ``Składowe rachunku`` charges table.

    ``is_summary`` marks the closing rows (Suma, Do zapłaty, Zapłacono,
    Aktualne saldo); charge rows like Czesne or meal lines are ordinary
    items even when they dominate the total.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    amount_pln: Decimal | None = None
    count: int | None = None
    is_summary: bool = False


class SettlementMonth(BaseModel):
    """One month card from the settlement history page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount_pln: Decimal
    saldo_pln: Decimal
    due_date: date | None = None
    status: SettlementStatus
    items: list[SettlementItem] = Field(default_factory=list)
    # Bill uuid from the "Pobierz fakturę" link (drives the invoice GET);
    # absent when the card carries no invoice link.
    invoice_bill_uuid: str | None = None


class SettlementHistoryPage(BaseModel):
    """All month cards parsed from one history page fetch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    months: list[SettlementMonth] = Field(default_factory=list)


__all__ = [
    "SettlementHistoryPage",
    "SettlementItem",
    "SettlementMonth",
    "SettlementStatus",
    "parse_due_date",
    "parse_money",
    "parse_month_key",
]
