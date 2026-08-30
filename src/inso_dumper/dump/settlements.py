"""Settlements dump writer: one private month directory per key.

The dump contract for a single settlement month (design.md "Writer"):
``<dir>/settlement.json`` (parsed card, Decimals as strings) and, when
an invoice exists, ``<dir>/invoice.pdf``. Writes go through the shared
atomic ``_write_bytes``; ``OSError`` is caught here and returned as
``Err(INTERNAL, 'settlements_write')`` — no partial files, no raw
exceptions past the boundary. Skip/presence logic lives in the
orchestrator, not here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from inso_dumper._result import Err, Ok
from inso_dumper.dump.writer import _write_bytes, ensure_private_dir
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.models.settlements import SettlementMonth

log = logging.getLogger("inso_dumper.dump.settlements")

_FILE_MODE = 0o600
_DIR_MODE = 0o700


def _write_month_json(month: SettlementMonth, dir_path: Path) -> None:
    """Serialize ``month`` to the design's ``settlement.json`` shape.

    ``model_dump(mode='json')`` yields Decimals as strings, the date as
    ISO, and the status as its StrEnum value; the nullable top-level
    ``due_date`` and ``invoice_bill_uuid`` keys are omitted when unset
    (item ``count: null`` is kept — the design example includes it).
    Raises ``OSError``.
    """
    data = month.model_dump(mode="json")
    if month.due_date is None:
        data.pop("due_date")
    if month.invoice_bill_uuid is None:
        data.pop("invoice_bill_uuid")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _write_bytes(dir_path / "settlement.json", payload.encode("utf-8"), _FILE_MODE)


def write_settlement_month(month: SettlementMonth, dir_path: Path) -> CliResult[Path]:
    """Write ``settlement.json`` into the private month directory."""
    try:
        ensure_private_dir(dir_path, parents=True)
        _write_month_json(month, dir_path)
    except OSError:
        log.warning("cannot write settlement month %s under %s", month.month, dir_path)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="settlements_write"))
    log.info("wrote %s", dir_path / "settlement.json")
    return Ok(dir_path)


def write_invoice(data: bytes, dir_path: Path) -> CliResult[Path]:
    """Write ``invoice.pdf`` into the month directory, atomically."""
    try:
        ensure_private_dir(dir_path, parents=True)
        _write_bytes(dir_path / "invoice.pdf", data, _FILE_MODE)
    except OSError:
        log.warning("cannot write invoice under %s", dir_path)
        return Err(CliError(kind=CliErrorKind.INTERNAL, subject="settlements_write"))
    log.info("wrote %s", dir_path / "invoice.pdf")
    return Ok(dir_path)


def invoice_path(dir_path: Path) -> Path:
    """Where the invoice for ``dir_path`` lives (the orchestrator's
    on-disk presence check)."""
    return dir_path / "invoice.pdf"


__all__ = ["invoice_path", "write_invoice", "write_settlement_month"]
