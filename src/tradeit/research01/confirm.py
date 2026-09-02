"""Confirming a ticker-to-CIK candidate from the filing's own words.

**The route that is sound.** `ticker_cik.py` proposes candidates by name and can
only refute them; nothing there establishes identity, because
``MappingEvidence.NAME_MATCH`` is ranked weakest in this codebase and declared
insufficient alone. What *is* sufficient is
``MappingEvidence.FILING_DOCUMENT_TEXT`` -- the registrant's own annual report
saying which symbol its stock trades under:

    "The Company's Common Stock is traded under the symbol \\"ABDR\\" in the
     Nasdaq National Market System"

That sentence, filed by CIK 1021080, binds that registrant to ``ABDR``. It is
evidence in a way a name match never is.

**This produces ``RESOLVED``, never ``MANUAL_VERIFIED``.** A program that reads
a filing has not satisfied the rule that a *person* read it -- ``acquire.py``
says so and imports nothing from the evidence layer so the boundary is
structural. Confirmations here are machine-read and graded accordingly.

**Two ways this could confirm the wrong thing, both refused:**

* A short ticker matching incidental prose. ``AK`` appears in ordinary English,
  so a bare substring search would confirm almost anything. The ticker must
  appear as a whole token *inside a sentence the extractor already identified as
  binding a security to a symbol*.
* A filing naming several symbols -- a company with common stock, warrants and
  units lists three. Where the statements name more than one distinct candidate
  symbol, the result is **ambiguous rather than the first match**, because
  picking one would be the splice in miniature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tradeit.edgar.acquire import EvidenceExtract
from tradeit.edgar.identity import MappingEvidence

__all__ = ["Confirmation", "candidate_symbols", "confirm_ticker"]

#: Words that can follow "symbol" without being one -- "symbol for", "symbol
#: on the New York Stock Exchange". Short now, and short *because* extraction is
#: precise: the long list this replaced was compensating for a regex that read
#: whole sentences.
_STOPWORDS = frozenset({"THE", "FOR", "ON", "OF", "AND", "IN", "AT", "IS", "A", "AN", "OUR", "ITS"})


#: How a filing actually binds a symbol: the word "symbol" (or "ticker"), then
#: optional punctuation and quoting, then the symbol itself.
#:
#: **The trailing lookahead refuses a venue-qualified form, and it is there
#: because this returned a wrong answer on two real filings.** Xplore
#: Technologies wrote "listed for trading on the Toronto Stock Exchange under
#: the symbol TSX: XPL"; Silvermex Resources wrote "under the symbol TSX-V:
#: GGC". In both the symbol is the half after the colon and ``TSX`` is the
#: exchange. Asked whether either filing bound ``TSX``, the pattern said yes,
#: and a successor search was one step from cutting seventeen years off a series
#: on the strength of it.
#:
#: The first attempt at this refused only ``TSX:`` and let ``TSX-V:`` straight
#: through, because the capture stops at the word boundary before the hyphen --
#: so the lookahead skips a bounded run of venue characters before demanding the
#: colon. A token followed by *any* venue tail and a colon and another symbol is
#: the venue half of ``VENUE: TICKER``, and never the ticker.
#:
#: The refusal is narrowed by ``_STOPWORDS`` so that a colon introducing
#: ordinary prose -- "under the symbol ABC: the shares are ..." -- still binds
#: ``ABC``. The whole text is upper-cased before matching, so without that
#: narrowing "the" and "TICKER" are indistinguishable to the lookahead.
#:
#: The qualified form is **refused rather than parsed**. Capturing ``XPL`` from
#: it would be more useful and is a wider change than a defect of this shape
#: warrants; returning nothing is the answer that cannot be wrong.
#:
#: **Extracting the bound token rather than every capitalised word in the
#: sentence** is what makes this usable. A whole-sentence scan of
#: `The Company's Common Stock is traded under the symbol "ABDR"` yields
#: `S` -- from `Company's` -- alongside `ABDR`, and a rule that refused
#: multi-symbol sentences then rejected 100% of a real 40-filing sample. The
#: answer was not a longer stopword list, which is whack-a-mole against English,
#: but asking the narrower question the filing already answers.
_BOUND_SYMBOL = re.compile(
    r"(?:TICKER\s+)?SYMBOLS?\s*[:\-,]?\s*"
    r"[\"\u201c\u2018']?\s*([A-Z]{1,6}(?:\.[A-Z]{1,2})?)\b"
    # The stopword alternation needs \b or `A` matches the start of `ABC` and
    # the refusal quietly stops working for every ticker beginning with a
    # stopword's first letters.
    rf"(?!\s*[-.A-Z0-9]{{0,8}}\s*:\s*(?!(?:{'|'.join(sorted(_STOPWORDS))})\b)[A-Z])"
)


#: EODHD disambiguates a reused ticker two ways: ``ABTC_old``, ``AVIR_old1``
#: **and** a bare trailing digit, ``AAP1``, ``AED2``. Both mean "another company
#: holds the plain symbol now" -- measured: 90.2% of trailing-digit tickers have
#: a base that exists as a *different* company (``AAP1`` Amway Asia Pacific
#: against ``AAP`` Advance Auto Parts).
_VENDOR_DISAMBIGUATOR = re.compile(r"(?:_OLD\d*|\d+)$", re.IGNORECASE)


def comparison_form(vendor_symbol: str) -> str:
    """The symbol as the *filing* would write it, for comparison only.

    A registrant's 10-K says ``AAP``; the vendor calls that series ``AAP1``
    because a later company took the plain symbol. Comparing the vendor's label
    to the filing therefore needs the disambiguator removed -- **and storing
    that stripped form is a different thing entirely, and was a bug.**
    """
    return _VENDOR_DISAMBIGUATOR.sub("", vendor_symbol.strip().upper())


@dataclass(frozen=True, slots=True)
class Confirmation:
    """A ticker bound to a CIK by a sentence, with that sentence kept verbatim.

    ``ticker`` is the **vendor's full symbol**, because that is what the vendor
    must be queried with. ``matched_as`` is the form the filing actually used.

    **They are separate fields because collapsing them spliced 92 securities.**
    An earlier version stored the comparison form, so ``ABTC_old`` -- a company
    that died -- was recorded as ``ABTC``, and the backfill then fetched the
    *living* ``ABTC`` and filed its prices under the dead registrant's CIK.
    388,590 bars, 20.7% of the corpus, attributed to the wrong company by the
    very code meant to prevent exactly that.
    """

    ticker: str
    cik: int
    #: The filing's own words. **Never a paraphrase** -- a summary of a filing
    #: is not a filing, and a citation nobody can check is not provenance.
    citation: str
    accession: str
    #: The form the filing used, after the vendor's disambiguator was removed.
    #: Provenance for *why* this pairing was accepted; never used to query.
    matched_as: str = ""
    evidence: MappingEvidence = MappingEvidence.FILING_DOCUMENT_TEXT


def candidate_symbols(extract: EvidenceExtract) -> set[str]:
    """Every token in the extract that could be a symbol.

    Drawn only from sentences the extractor already judged to bind a security
    to a symbol, and from Section 12(b) rows -- not from the whole document.
    A document-wide scan would find a ticker somewhere in almost any filing.
    """
    found: set[str] = set()
    texts = [s.text for s in extract.symbol_statements]
    # A 12(b) row is raw `cells` -- the filing's own rendering, deliberately
    # not parsed into named columns, so every cell is searched.
    texts += [" ".join(row.cells) for row in extract.section_12b_rows]
    for text in texts:
        for token in _BOUND_SYMBOL.findall(text.upper()):
            if token not in _STOPWORDS:
                found.add(token)
    return found


def confirm_ticker(
    *, ticker: str, cik: int, accession: str, extract: EvidenceExtract
) -> Confirmation | None:
    """Confirm this ticker against this filing, or return ``None``.

    ``None`` covers three different situations on purpose -- no symbol sentence,
    a sentence naming a different symbol, and a sentence naming several. All
    three mean "not established", and distinguishing them would invite treating
    the near-misses as partial evidence.
    """
    vendor_symbol = ticker.strip().upper()
    wanted = comparison_form(vendor_symbol)
    if not wanted:
        return None

    symbols = candidate_symbols(extract)
    if wanted not in symbols:
        return None
    if len(symbols) > 1:
        # A filing naming common stock, warrants and units names three symbols.
        # Choosing the one we hoped for is confirmation bias with a citation.
        return None

    for statement in extract.symbol_statements:
        if re.search(rf"\b{re.escape(wanted)}\b", statement.text.upper()):
            return Confirmation(
                ticker=vendor_symbol,
                cik=cik,
                citation=statement.text.strip(),
                accession=accession,
                matched_as=wanted,
            )
    for row in extract.section_12b_rows:
        blob = " ".join(row.cells)
        if re.search(rf"\b{re.escape(wanted)}\b", blob.upper()):
            return Confirmation(ticker=wanted, cik=cik, citation=blob.strip(), accession=accession)
    return None
