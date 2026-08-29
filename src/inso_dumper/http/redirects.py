"""Shared redirect-following helpers for HTTP shells.

``HttpxClient`` deliberately does not auto-follow redirects so the login
flow can observe each hop. Shells that just want the final resource use
the helpers here instead: bounded, same-origin-checked redirect loops.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urljoin, urlparse

# The spike's observed login chain is /login -> /panel/ ->
# /panel/home/<uuid>/, i.e. 2 hops; 5 is a 2.5x safety margin that
# bounds a redirect-storm DoS in seconds, not unbounded.
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def header_value(headers: Sequence[tuple[str, str]], name: str) -> str | None:
    """Case-insensitive single-header lookup; ``None`` when absent."""
    needle = name.lower()
    for k, v in headers:
        if k.lower() == needle:
            return v
    return None


def is_same_origin(url: str, base: str) -> bool:
    """True when ``url`` resolves to the same scheme+host+port as ``base``.

    A naive ``startswith(base)`` accepts ``https://app.inso.pl.attacker.tld``
    as a prefix of ``https://app.inso.pl``; that would send the
    Cloudflare-fingerprint headers (and the session cookie) to a host
    derived from a server-supplied string. urlparse compares the parsed
    netlocs instead.
    """
    base_parsed = urlparse(base)
    url_parsed = urlparse(url)
    return (
        url_parsed.scheme == base_parsed.scheme
        and (url_parsed.hostname or "").lower() == (base_parsed.hostname or "").lower()
        and (url_parsed.port or base_parsed.port) == (base_parsed.port or url_parsed.port)
    )


def resolve_location(current_url: str, base: str, location: str) -> str | None:
    """Resolve a ``Location`` header against ``current_url``.

    Returns the absolute URL, or ``None`` when it points off-origin
    (a server anomaly the caller must surface, never follow — following
    would send the session cookie to a foreign host).
    """
    next_url = urljoin(current_url, location)
    return next_url if is_same_origin(next_url, base) else None


__all__ = [
    "MAX_REDIRECTS",
    "REDIRECT_STATUSES",
    "header_value",
    "is_same_origin",
    "resolve_location",
]
