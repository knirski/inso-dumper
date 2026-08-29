"""Unit tests for the token-redacting logging filter."""

from __future__ import annotations

import logging

from inso_dumper.logging_setup import TokenRedactingFilter


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="inso_dumper.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_filter_strips_phpsessid_value() -> None:
    f = TokenRedactingFilter()
    rec = _record("login ok PHPSESSID=d9f828d2629433b8d1b9690a17d477e3; extra")
    f.filter(rec)
    assert "d9f828d2629433b8d1b9690a17d477e3" not in rec.msg
    assert "PHPSESSID=<redacted>" in rec.msg


def test_filter_strips_password_value() -> None:
    f = TokenRedactingFilter()
    rec = _record("auth attempt password=hunter2 user=alice")
    f.filter(rec)
    assert "hunter2" not in rec.msg
    assert "password=<redacted>" in rec.msg


def test_filter_strips_set_cookie_value() -> None:
    f = TokenRedactingFilter()
    rec = _record("got Set-Cookie: PHPSESSID=abcdef; path=/")
    f.filter(rec)
    assert "abcdef" not in rec.msg
    assert "<redacted>" in rec.msg


def test_filter_strips_bearer_token() -> None:
    f = TokenRedactingFilter()
    rec = _record("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
    f.filter(rec)
    assert "eyJhbGciOiJIUzI1NiJ9" not in rec.msg


def test_filter_strips_secret_value() -> None:
    f = TokenRedactingFilter()
    rec = _record("api secret=super-secret-value")
    f.filter(rec)
    assert "super-secret-value" not in rec.msg


def test_filter_is_noop_for_unrelated_strings() -> None:
    f = TokenRedactingFilter()
    rec = _record("GET /panel/home/abc-123/ status=200 duration=0.45s")
    f.filter(rec)
    assert rec.msg == "GET /panel/home/abc-123/ status=200 duration=0.45s"


def test_filter_is_case_insensitive() -> None:
    f = TokenRedactingFilter()
    rec = _record("Cookie: SESSION=opaque-value;")
    f.filter(rec)
    assert "opaque-value" not in rec.msg


def test_filter_handles_no_match() -> None:
    f = TokenRedactingFilter()
    rec = _record("plain log message")
    f.filter(rec)
    assert rec.msg == "plain log message"


def test_filter_scrubs_exception_traceback_text() -> None:
    """exc_text (the formatted traceback) must also be scrubbed."""
    f = TokenRedactingFilter()
    rec = logging.LogRecord(
        name="inso_dumper.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="boom",
        args=(),
        exc_info=None,
    )
    # Simulate what stdlib does: exc_text holds the formatted traceback,
    # including any exception message that may embed a token.
    rec.exc_text = "Traceback (most recent call last):\n  ...\nRuntimeError: PHPSESSID=abcdef123 leaked\n"
    f.filter(rec)
    assert rec.exc_text is not None
    assert "abcdef123" not in rec.exc_text
    assert "PHPSESSID=<redacted>" in rec.exc_text
