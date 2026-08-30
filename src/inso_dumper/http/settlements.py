"""Settlements HTTP layer: history-page parser and fetch shells.

The Rozliczenia UI is fully server-rendered HTML (no JSON API —
verified Aug 2026, see api-notes.md § Settlements). The parser is a
stdlib ``HTMLParser`` state machine over the captured card structure:

- month label ``<div class="text-lg font-semibold ...">Sierpień 2026</div>``
- card ``<div class="bg-white shadow overflow-hidden rounded-lg ...">``
  with a status badge span, an optional details table (charge rows +
  invoice link) and a Kwota/Saldo/Termin summary grid
- the "Pobierz fakturę" link carries the bill uuid:
  ``/panel/settlement/<bill-uuid>/invoice``

Everything unexpected is drift: the functional core fails loud instead
of guessing.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlparse

from inso_dumper._result import Err, Ok, Result
from inso_dumper.config import Config
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.client import HttpClient, Response
from inso_dumper.http.redirects import (
    MAX_REDIRECTS,
    REDIRECT_STATUSES,
    header_value,
    resolve_location,
)
from inso_dumper.models.session import Session
from inso_dumper.models.settlements import (
    SettlementHistoryPage,
    SettlementItem,
    SettlementMonth,
    SettlementStatus,
    parse_due_date,
    parse_money,
    parse_month_key,
)

_MONTH_LABEL_MARKERS = ("text-lg", "font-semibold")
_CARD_MARKERS = ("bg-white", "rounded-lg")
_GRID_MARKER = "grid-cols-7"
_GRID_ITEM_MARKER = "col-span-"
_BADGE_MARKER = "bg-green-100"
_INVOICE_RE = re.compile(r"/panel/settlement/([0-9a-fA-F-]+)/invoice")
_COUNT_RE = re.compile(r"\((\d+)\s+sztuki?\)")
_PAID_BADGE = "opłacone"
_SUMMARY_LABELS = {"Suma", "Do zapłaty", "Zapłacono", "Aktualne saldo"}


def _drift(kind: CliErrorKind, subject: str) -> Err[CliError]:
    return Err(CliError(kind=kind, subject=subject))


class _Card:
    """Mutable accumulation buffer for one month card."""

    __slots__ = (
        "amount_text",
        "badge_text",
        "bill_uuid",
        "due_text",
        "label_text",
        "rows",
        "saldo_text",
    )

    def __init__(self) -> None:
        self.label_text = ""
        self.badge_text = ""
        self.amount_text = ""
        self.saldo_text = ""
        self.due_text = ""
        self.rows: list[tuple[str, str]] = []
        self.bill_uuid: str | None = None


class _SettlementsParser(HTMLParser):
    """Depth-tracking state machine. Text is appended to every open
    buffer; the buffers model disjoint DOM regions (label div, badge
    span, table cells, summary-grid items), so no text is counted
    twice."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self.cards: list[_Card] = []
        self._pending_label: str | None = None
        # Open-context bookkeeping: depth at which each context started
        # (-1 = closed) plus its text buffer.
        self._label_depth = -1
        self._label_buf: list[str] = []
        self._card_depth = -1
        self._card: _Card | None = None
        self._badge_depth = -1
        self._badge_buf: list[str] = []
        self._grid_depth = -1
        self._item_depth = -1
        self._item_buf: list[str] = []
        self._row_open = False
        self._cells: list[str] = []
        self._cell_buf: list[str] = []
        self._cell_open = False

    # -- helpers ------------------------------------------------------------

    def _classes(self, attrs: list[tuple[str, str | None]]) -> str:
        for key, value in attrs:
            if key == "class" and value:
                return value
        return ""

    def _attr(self, attrs: list[tuple[str, str | None]], key: str) -> str | None:
        for k, value in attrs:
            if k == key and value is not None:
                return value
        return None

    # -- tag handlers ---------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        if tag == "div":
            self._depth += 1
            in_card = self._card_depth != -1
            if not in_card and all(marker in classes for marker in _MONTH_LABEL_MARKERS):
                self._label_depth = self._depth
                self._label_buf = []
            elif not in_card and all(marker in classes for marker in _CARD_MARKERS):
                if self._pending_label is None:
                    # A card without a preceding month label is drift;
                    # keep parsing and let finalization report it.
                    self._pending_label = ""
                self._card_depth = self._depth
                self._card = _Card()
                self._card.label_text = self._pending_label
                self._pending_label = None
            elif in_card and _GRID_MARKER in classes and self._grid_depth == -1:
                self._grid_depth = self._depth
            elif self._grid_depth != -1 and _GRID_ITEM_MARKER in classes:
                self._item_depth = self._depth
                self._item_buf = []
        elif tag == "span" and self._card_depth != -1 and _BADGE_MARKER in classes:
            self._badge_depth = self._depth
            self._badge_buf = []
        elif tag == "table" and self._card_depth != -1:
            self._row_open = False
        elif tag == "tr" and self._card_depth != -1 and self._grid_depth == -1:
            self._row_open = True
            self._cells = []
        elif tag == "td" and self._row_open:
            self._cell_open = True
            self._cell_buf = []
        elif tag == "a" and self._card_depth != -1 and self._card is not None:
            href = self._attr(attrs, "href") or ""
            match = _INVOICE_RE.search(href)
            if match and self._card.bill_uuid is None:
                self._card.bill_uuid = match.group(1)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            if self._item_depth != -1 and self._depth <= self._item_depth:
                self._finish_grid_item()
            if self._grid_depth != -1 and self._depth <= self._grid_depth:
                self._grid_depth = -1
            if self._label_depth != -1 and self._depth <= self._label_depth:
                self._pending_label = "".join(self._label_buf).strip() or None
                self._label_depth = -1
            if self._card_depth != -1 and self._depth <= self._card_depth:
                self._finish_card()
            self._depth = max(0, self._depth - 1)
        elif tag == "span":
            if self._badge_depth != -1 and self._depth <= self._badge_depth:
                if self._card is not None:
                    self._card.badge_text = "".join(self._badge_buf).strip()
                self._badge_depth = -1
        elif tag == "td" and self._cell_open:
            self._cells.append("".join(self._cell_buf).strip())
            self._cell_open = False
        elif tag == "tr" and self._row_open:
            self._row_open = False
            if self._card is not None and len(self._cells) >= 2:
                label, amount = self._cells[0], self._cells[1]
                if label and amount:
                    self._card.rows.append((label, amount))

    def handle_data(self, data: str) -> None:
        if not data.strip() and not data:
            return
        if self._label_depth != -1:
            self._label_buf.append(data)
        if self._badge_depth != -1:
            self._badge_buf.append(data)
        if self._cell_open:
            self._cell_buf.append(data)
        if self._item_depth != -1:
            self._item_buf.append(data)

    # -- finalization ---------------------------------------------------------

    def _finish_grid_item(self) -> None:
        self._item_depth = -1
        text = " ".join("".join(self._item_buf).split())
        self._item_buf = []
        if self._card is None:
            return
        label, _, rest = text.partition(" ")
        if label == "Kwota":
            self._card.amount_text = rest
        elif label == "Saldo":
            self._card.saldo_text = rest
        elif label == "Termin":
            self._card.due_text = rest

    def _finish_card(self) -> None:
        self._card_depth = -1
        card = self._card
        self._card = None
        self._grid_depth = -1
        self._badge_depth = -1
        if card is not None:
            self.cards.append(card)

    def months(self) -> Result[SettlementHistoryPage, CliError]:
        """Fold the collected cards into a validated page or drift."""
        months: list[SettlementMonth] = []
        seen: set[str] = set()
        for card in self.cards:
            if not card.label_text:
                return _drift(CliErrorKind.PLATFORM_CHANGED, "settlements_month_card")
            match parse_month_key(card.label_text):
                case Ok(value):
                    month_key = value
                case err:
                    return err
            if month_key in seen:
                return _drift(CliErrorKind.PLATFORM_CHANGED, "settlements_duplicate_month")
            seen.add(month_key)
            if not card.amount_text or not card.saldo_text:
                return _drift(CliErrorKind.PLATFORM_CHANGED, "settlements_month_card")
            match parse_money(card.amount_text):
                case Ok(value):
                    amount = value
                case err:
                    return err
            match parse_money(card.saldo_text):
                case Ok(value):
                    saldo = value
                case err:
                    return err
            due_date: date | None = None
            if card.due_text.strip():
                match parse_due_date(card.due_text):
                    case Ok(value):
                        due_date = value
                    case err:
                        return err
            items: list[SettlementItem] = []
            for label, amount_text in card.rows:
                match parse_money(amount_text):
                    case Ok(value):
                        row_amount = value
                    case err:
                        return err
                count_match = _COUNT_RE.search(label)
                clean_label = _COUNT_RE.sub("", label).replace(":", " ").strip()
                clean_label = " ".join(clean_label.split())
                items.append(
                    SettlementItem(
                        label=clean_label,
                        amount_pln=row_amount,
                        count=int(count_match.group(1)) if count_match else None,
                        is_summary=clean_label in _SUMMARY_LABELS,
                    )
                )
            status = (
                SettlementStatus.PAID
                if card.badge_text == _PAID_BADGE
                else SettlementStatus.UNKNOWN
            )
            months.append(
                SettlementMonth(
                    month=month_key,
                    amount_pln=amount,
                    saldo_pln=saldo,
                    due_date=due_date,
                    status=status,
                    items=items,
                    invoice_bill_uuid=card.bill_uuid,
                )
            )
        return Ok(SettlementHistoryPage(months=months))


def parse_settlements_history(html: str) -> Result[SettlementHistoryPage, CliError]:
    """Parse one settlement history page into typed months.

    Pure function: no IO, no httpx. Any structural surprise is returned
    as ``Err(PLATFORM_CHANGED, ...)`` — never a partial dump.
    """
    parser = _SettlementsParser()
    parser.feed(html)
    parser.close()
    return parser.months()


# --- fetch shells -----------------------------------------------------------


def _points_at_login(url: str) -> bool:
    """True when ``url`` is the login page (any query string).

    Mirrors ``children_shell``: the platform answers a logged-out
    request with ``302 Location: /login`` instead of a 401 — the
    redirect target is the session-expired signal on the HTML path.
    """
    return urlparse(url).path.rstrip("/") == "/login"


async def _get_following_redirects(
    client: HttpClient, config: Config, session: Session, path: str, subject: str
) -> CliResult[Response]:
    """GET ``path`` following bounded same-origin redirects.

    A redirect whose target is the login page is
    ``Err(SESSION_EXPIRED, <subject>)``; an off-origin redirect target
    is never followed (``Err(HTTP, 'off_domain_redirect')``); a
    redirect storm is ``Err(HTTP, 'redirect_loop')``. The client's own
    Err values propagate unchanged.
    """
    base = str(config.base_url).rstrip("/")
    headers = {"Cookie": f"PHPSESSID={session.phpsessid}"}
    result = await client.request("GET", path, headers=headers)
    current_url = f"{base}{path}"
    response: Response | None = None
    match result:
        case Err(error):
            return Err(error)
        case Ok(value):
            response = value

    for _ in range(MAX_REDIRECTS):
        if response.status not in REDIRECT_STATUSES:
            break
        location = header_value(response.headers, "location")
        if not location:
            break
        next_url = resolve_location(current_url, base, location)
        if next_url is None:
            return Err(CliError(kind=CliErrorKind.HTTP, subject="off_domain_redirect"))
        if _points_at_login(next_url):
            return Err(CliError(kind=CliErrorKind.SESSION_EXPIRED, subject=subject))
        current_url = next_url
        next_path = next_url[len(base) :] or "/"
        result = await client.request("GET", next_path, headers=headers)
        match result:
            case Err(error):
                return Err(error)
            case Ok(value):
                response = value
    else:
        if response.status in REDIRECT_STATUSES and header_value(response.headers, "location"):
            return Err(CliError(kind=CliErrorKind.HTTP, subject="redirect_loop"))

    return Ok(response)


async def fetch_settlements_history(
    client: HttpClient, config: Config, session: Session, child_uuid: str
) -> AsyncIterator[CliResult[SettlementHistoryPage]]:
    """Fetch and parse the child's full settlement history page.

    Yields exactly one item: ``Ok(page)`` on success; ``Err`` on
    transport failure, non-200 (``Err(HTTP, 'settlements_status_<n>')``),
    expired session (login redirect), or parser drift. GET-only — the
    history URL is verified by the Aug 2026 spike (api-notes.md
    § Settlements); the current-month page is never requested (derivable
    from the history page).
    """
    path = f"/panel/child/{child_uuid}/settlement"
    result = await _get_following_redirects(
        client, config, session, path, subject="settlements_list"
    )
    match result:
        case Err(error):
            yield Err(error)
            return
        case Ok(response):
            pass
    if response.status != 200:
        yield Err(CliError(kind=CliErrorKind.HTTP, subject=f"settlements_status_{response.status}"))
        return
    yield parse_settlements_history(response.text())


async def fetch_invoice(
    client: HttpClient, config: Config, session: Session, bill_uuid: str
) -> CliResult[bytes]:
    """Download one invoice PDF.

    ``GET /panel/settlement/<bill-uuid>/invoice`` returns the raw PDF
    body with a session cookie (same-origin, no CDN signing — verified
    Aug 2026). Non-200 is ``Err(HTTP, 'invoice_status_<n>')``; a
    non-``application/pdf`` body is ``Err(PLATFORM_CHANGED,
    'invoice_content_type')`` so a drifted response never lands in the
    dump as a corrupt ``invoice.pdf``.
    """
    path = f"/panel/settlement/{bill_uuid}/invoice"
    result = await _get_following_redirects(
        client, config, session, path, subject="settlements_invoice"
    )
    match result:
        case Err(error):
            return Err(error)
        case Ok(response):
            pass
    if response.status != 200:
        return Err(CliError(kind=CliErrorKind.HTTP, subject=f"invoice_status_{response.status}"))
    content_type = (header_value(response.headers, "content-type") or "").lower()
    if "application/pdf" not in content_type:
        return _drift(CliErrorKind.PLATFORM_CHANGED, "invoice_content_type")
    return Ok(response.body)


__all__ = [
    "fetch_invoice",
    "fetch_settlements_history",
    "parse_settlements_history",
]
