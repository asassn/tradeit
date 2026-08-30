"""Keeping credentials out of everything the tool writes down.

Lives in its own module because more than one adapter needs it and because a
copy-pasted second version would drift. Twelve Data makes this load-bearing
rather than defensive: its API takes the key as an ``apikey`` **query
parameter**, not a header, so every URL this tool handles genuinely contains a
secret and every one of them has to be scrubbed before it reaches a journal
line, a cache sidecar, a log or an exception message.

The list is of parameter *names* rather than value shapes, because a key looks
like any other opaque string and matching on shape would either miss real keys
or redact real data.
"""

from __future__ import annotations

import re

#: Query parameters whose value must never be printed, whatever a vendor calls
#: them. Matched case-insensitively. Add to this list rather than writing a
#: second redactor.
CREDENTIAL_PARAMS: tuple[str, ...] = (
    "apikey",
    "api_key",
    "api_token",
    "token",
    "access_key",
    "auth_token",
    "key",
)

_CREDENTIAL_RE = re.compile(rf"(?i)\b({'|'.join(CREDENTIAL_PARAMS)})=[^&\s]*")


def redact_url(url: str) -> str:
    """Replace any credential-shaped query parameter's value with ``REDACTED``.

    Order matters in :data:`CREDENTIAL_PARAMS`: ``apikey`` and ``api_key``
    precede the bare ``key`` so the longer names win the alternation and
    ``api_key=x`` does not come out as ``api_REDACTED``.
    """
    return _CREDENTIAL_RE.sub(lambda match: f"{match.group(1)}=REDACTED", url)


def redact_text(text: str, secret: str = "") -> str:
    """Scrub a message before it is stored.

    Redacts credential-shaped parameters, and additionally any literal
    occurrence of ``secret`` — which catches the case a URL-shaped regex cannot:
    a vendor that echoes the key back inside a JSON error body.
    """
    cleaned = redact_url(text)
    if secret and secret in cleaned:
        cleaned = cleaned.replace(secret, "REDACTED")
    return cleaned


def credential_hint(secret: str) -> str:
    """A non-secret acknowledgement that a key is present.

    Length and last two characters only. Enough to tell "the variable is set to
    something" from "the variable is empty", and useless to anyone reading it
    over a shoulder or in a pasted terminal transcript.
    """
    if not secret:
        return "not set"
    return f"set ({len(secret)} chars, ends …{secret[-2:]})"


__all__ = ["CREDENTIAL_PARAMS", "credential_hint", "redact_text", "redact_url"]
