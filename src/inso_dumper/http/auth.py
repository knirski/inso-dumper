"""Pure login parsers: form payload construction and response decoding.

Functional core: no IO, no httpx. The shell in
``inso_dumper.http.login`` orchestrates the network calls and feeds
the resulting Response into ``parse_login_response``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import ValidationError

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliError, CliErrorKind, CliResult
from inso_dumper.http.client import Response
from inso_dumper.models.session import Session

# UUID v1/v4/v5 pattern, dashed, lowercase. The spike's user UUIDs are
# v1-style; the regex accepts any UUID-shaped string.
_USER_UUID_RE: Final = re.compile(
    r"/panel/home/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?",
    re.IGNORECASE,
)

# Set-Cookie value: ``PHPSESSID=<opaque>`` possibly quoted. The opaque
# value runs to the next semicolon, comma, or whitespace. RFC 6265
# allows the value to be either a token or a quoted-string; we strip
# surrounding quotes so the captured value is usable as a cookie.
_PHPSESSID_RE: Final = re.compile(
    r'PHPSESSID\s*=\s*(?:"(?P<quoted>[^"]*)"|(?P<token>[^;,\s]+))',
    re.IGNORECASE,
)


def build_login_payload(email: str, password: str) -> Mapping[str, str]:
    """Build the form-encoded body for ``POST /login``.

    The Inso login form uses Symfony's default field names
    (``_username``, ``_password``) with no CSRF token — verified in
    ``docs/api-notes.md``.
    """
    return {"_username": email, "_password": password}


def _extract_phpsessid(headers: Sequence[tuple[str, str]]) -> str | None:
    for name, value in headers:
        if name.lower() != "set-cookie":
            continue
        m = _PHPSESSID_RE.search(value)
        if m:
            # Quoted-string wins if matched; otherwise the token.
            return m.group("quoted") if m.group("quoted") is not None else m.group("token")
    return None


def _extract_user_uuid(final_url: str) -> str | None:
    m = _USER_UUID_RE.search(final_url)
    if m:
        return m.group("uuid")
    return None


def parse_login_response(response: Response, *, final_url: str) -> CliResult[Session]:
    """Decode the post-login landing response into a Session.

    ``final_url`` is the URL of the response after the redirect chain
    has been followed (e.g. ``/panel/home/<uuid>/``). The shell in
    ``inso_dumper.http.login`` tracks this.

    Returns ``Err(AUTH)`` if PHPSESSID is missing or the user UUID
    cannot be extracted from the final URL. A pydantic ValidationError
    on the constructed Session is bucketed as ``AUTH`` (the cookie
    is the user input; bad shape is the user / server's fault, not
    ours) — any other exception propagates to the CLI's last-resort
    catch as INTERNAL.
    """
    sid = _extract_phpsessid(response.headers)
    if sid is None:
        return Err(CliError(kind=CliErrorKind.AUTH, subject="missing_phpsessid"))
    uuid = _extract_user_uuid(final_url)
    if uuid is None:
        return Err(CliError(kind=CliErrorKind.AUTH, subject="no_user_uuid"))
    try:
        session = Session(phpsessid=sid, user_uuid=uuid)
    except ValidationError:
        return Err(CliError(kind=CliErrorKind.AUTH, subject="invalid_session"))
    return Ok(session)


__all__ = ["build_login_payload", "parse_login_response"]
