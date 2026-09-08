"""The ticker an insider filing states, in a field rather than a sentence.

Forms 3, 4 and 5 — the ownership filings an officer, director or large holder
submits — carry a structured issuer block:

```xml
<issuer>
  <issuerCik>0000320193</issuerCik>
  <issuerName>Apple Inc.</issuerName>
  <issuerTradingSymbol>AAPL</issuerTradingSymbol>
</issuer>
```

**This is the first route that does not read prose.** Every other source in this
corpus needs a sentence to be phrased a particular way; here the symbol is a
named field, and electronic filing of Form 4 has been mandatory since
2003-06-30, so the coverage is systematic rather than incidental. Measured
2026-09-07: 3,395 registrants with a dated exit and no ticker have at least one,
concentrated in 2005-2014 exactly as that mandate predicts.

Three guards, each from a real filing in the first sample of eight
--------------------------------------------------------------------

**The issuer CIK must match.** One sampled filing was indexed under CIK 1085359
and its own ``issuerCik`` named a different registrant — Brookfield Property
REIT. A filing appearing under a CIK is not a statement *by* that registrant,
which is the oldest rule in this codebase, and binding on the index alone would
have attributed ``GGP`` to the wrong company. **The document's own CIK is the
evidence; its location is not.**

**"NONE" is a value, not a symbol.** Three of the eight state
``<issuerTradingSymbol>NONE</issuerTradingSymbol>`` — the filer saying the issuer
has no trading symbol. Read as a ticker it produces the symbol ``NONE``; read
correctly it is *positive evidence of no listed security*, and is returned as
such so a caller can record it rather than discard it.

**A venue suffix is not part of the symbol.** ``MAJR.OB`` is ``MAJR`` on the OTC
Bulletin Board. The suffix is stripped — but only the known venue suffixes,
because ``BRK.A`` and ``BF.B`` carry a *share class* in the same position and
stripping that would merge two securities that trade at different prices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["InsiderSymbol", "InsiderVerdict", "read_insider_symbol"]

_ISSUER_CIK = re.compile(r"<issuerCik>\s*0*([0-9]+)\s*</issuerCik>", re.IGNORECASE)
_ISSUER_NAME = re.compile(r"<issuerName>\s*([^<]*)</issuerName>", re.IGNORECASE)
_SYMBOL = re.compile(r"<issuerTradingSymbol>\s*([^<]*)</issuerTradingSymbol>", re.IGNORECASE)

#: Venue qualifiers a filer appends to the symbol. ``.OB`` is the OTC Bulletin
#: Board, ``.PK`` the Pink Sheets, ``.OQ`` and ``.N`` Reuters venue codes.
#: **Deliberately an explicit list**: a rule that stripped any trailing
#: dot-suffix would turn ``BRK.A`` into ``BRK`` and merge Berkshire's two
#: classes, which trade three orders of magnitude apart.
_VENUE_SUFFIX = re.compile(r"\.(?:OB|PK|QB|QX|OQ|N|A[SX]|TO|V)$", re.IGNORECASE)

#: What a filer writes when the issuer has no trading symbol. Every one of these
#: is a statement, and none of them is a ticker.
_NO_SYMBOL = frozenset({"NONE", "N/A", "NA", "NIL", "NOT APPLICABLE", "NONE.", "-", "--"})

#: Decoration a filer wraps the value in: ``[NONE]``, ``"AAPL"``, ``(MERB)``.
#: Stripped before the value is judged, because a blocklist that has to
#: anticipate every rendering of "none" is a blocklist that will be beaten --
#: it already was, by ``[NONE]``, which bound a ticker of that name.
_WRAPPER = re.compile(r"^[\[\](){}<>\"'\u201c\u201d\s.]+|[\[\](){}<>\"'\u201c\u201d\s.]+$")

#: What a ticker can actually look like. **The real guard.** A value that is not
#: this shape is not a symbol, whatever it says, and asking what a ticker *is*
#: beats enumerating what it is not.
_TICKER_SHAPE = re.compile(r"^[A-Z][A-Z0-9]{0,6}(?:\.[A-Z]{1,2})?$")


class InsiderVerdict(StrEnum):
    """What one insider filing establishes."""

    #: A symbol, from a filing whose own issuerCik matches the registrant.
    SYMBOL = "symbol"
    #: The filing states the issuer has no trading symbol. Positive evidence.
    NO_TRADING_SYMBOL = "no_trading_symbol"
    #: The filing's own issuerCik names a different registrant. Establishes
    #: nothing about the one it was indexed under.
    CIK_MISMATCH = "cik_mismatch"
    #: No issuer block, or no symbol field. Says nothing either way.
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class InsiderSymbol:
    """The verdict, the symbol if there is one, and what the filing said."""

    verdict: InsiderVerdict
    symbol: str | None
    issuer_name: str
    #: The field's contents exactly as filed, before venue-suffix stripping.
    stated: str

    @property
    def binds(self) -> bool:
        return self.verdict is InsiderVerdict.SYMBOL and bool(self.symbol)


def read_insider_symbol(document: str, *, cik: int) -> InsiderSymbol:
    """Read one Form 3/4/5. ``cik`` is the registrant this must be about.

    The CIK is required rather than optional because the check it enables is
    the whole reason this route is evidence: without it, a filing indexed under
    one registrant would bind another registrant's symbol.
    """
    stated_cik = _ISSUER_CIK.search(document)
    name = _ISSUER_NAME.search(document)
    symbol = _SYMBOL.search(document)
    issuer_name = name.group(1).strip() if name else ""

    if stated_cik is None or symbol is None:
        return InsiderSymbol(InsiderVerdict.UNREADABLE, None, issuer_name, "")
    if int(stated_cik.group(1)) != cik:
        return InsiderSymbol(
            InsiderVerdict.CIK_MISMATCH, None, issuer_name, symbol.group(1).strip()
        )

    stated = symbol.group(1).strip()
    bare = _WRAPPER.sub("", stated).upper()
    if not bare or bare in _NO_SYMBOL:
        return InsiderSymbol(InsiderVerdict.NO_TRADING_SYMBOL, None, issuer_name, stated)

    cleaned = _VENUE_SUFFIX.sub("", bare).strip().upper()
    if not cleaned or cleaned in _NO_SYMBOL:
        return InsiderSymbol(InsiderVerdict.NO_TRADING_SYMBOL, None, issuer_name, stated)
    if not _TICKER_SHAPE.match(cleaned):
        # Not a ticker, whatever it says. Reported as unreadable rather than as
        # "no trading symbol", because a value we cannot parse is not the filer
        # telling us the issuer has none.
        return InsiderSymbol(InsiderVerdict.UNREADABLE, None, issuer_name, stated)
    return InsiderSymbol(InsiderVerdict.SYMBOL, cleaned, issuer_name, stated)
