"""Logging configuration and token-redacting filter.

The filter is installed at the root logger so individual call sites
cannot forget. It scrubs anything matching
``(?i)(token|cookie|set-cookie|password|secret)=<value>`` from record
messages before they hit the handler.
"""

from __future__ import annotations

import logging
import os
import re

from rich.logging import RichHandler

# Match KEY=VALUE where KEY is a known sensitive identifier. Greedy on
# the value up to the next whitespace, semicolon, or comma — covers
# form bodies, header values, and curl-style ``key=val key2=val2`` strings.
_SENSITIVE_KEY = re.compile(
    r"(?i)\b("
    r"token|access_token|refresh_token|csrf_token|bearer|"
    r"cookie|set-cookie|phpsessid|session|"
    r"password|secret"
    r")\s*=\s*([^\s;,]+)"
)
# Match ``Authorization: Bearer <opaque>`` style — no equals sign.
_SENSITIVE_BEARER = re.compile(r"(?i)\b(authorization\s*:\s*bearer)\s+(\S+)")
_REDACTED = "<redacted>"


class TokenRedactingFilter(logging.Filter):
    """Scrub sensitive values from log record messages."""

    @staticmethod
    def _scrub(text: str) -> str:
        text = _SENSITIVE_KEY.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)
        text = _SENSITIVE_BEARER.sub(lambda m: f"{m.group(1)} {_REDACTED}", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            # Render %-args and re-scrub the result so accidental
            # ``"PHPSESSID=%s" % (cookie,)`` does not leak.
            try:
                rendered = record.msg % record.args
            except Exception:
                rendered = record.msg
            if isinstance(rendered, str):
                scrubbed = self._scrub(rendered)
                record.msg = scrubbed
                record.args = ()
        return True


def setup_logging(verbose: bool = False) -> None:
    """Configure the root logger with rich-backed stderr output.

    Idempotent: calling twice does not double-install handlers.
    """
    root = logging.getLogger()
    # Remove any handlers we previously installed (so tests can call
    # setup_logging repeatedly without stacking output).
    for handler in list(root.handlers):
        if isinstance(handler, RichHandler):
            root.removeHandler(handler)

    level = logging.DEBUG if verbose else logging.INFO
    handler = RichHandler(
        level=level,
        console=None,  # default stderr
        show_path=False,
        show_time=False,
        markup=False,
    )
    handler.addFilter(TokenRedactingFilter())
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the project prefix."""
    return logging.getLogger(f"inso_dumper.{name}")


def is_verbose() -> bool:
    """True if the user requested DEBUG-level logging via env."""
    return os.environ.get("INSO_DUMPER_VERBOSE") == "1"


__all__ = [
    "TokenRedactingFilter",
    "get_logger",
    "is_verbose",
    "setup_logging",
]
