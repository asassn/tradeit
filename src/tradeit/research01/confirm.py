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

#: How a filing actually binds a symbol: the word "symbol" (or "ticker"), then
#: optional punctuation and quoting, then the symbol itself.
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
)


#: Words that can follow "symbol" without being one -- "symbol for", "symbol
#: on the New York Stock Exchange". Short now, and short *because* extraction is
#: precise: the long list this replaced was compensating for a regex that read
#: whole sentences.
_STOPWORDS = frozenset({"THE", "FOR", "ON", "OF", "AND", "IN", "AT", "IS", "A", "AN", "OUR", "ITS"})


@dataclass(frozen=True, slots=True)
class Confirmation:
    """A ticker bound to a CIK by a sentence, with that sentence kept verbatim."""

    ticker: str
    cik: int
    #: The filing's own words. **Never a paraphrase** -- a summary of a filing
    #: is not a filing, and a citation nobody can check is not provenance.
    citation: str
    accession: str
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
    wanted = ticker.split("_")[0].strip().upper()
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
                ticker=wanted, cik=cik, citation=statement.text.strip(), accession=accession
            )
    for row in extract.section_12b_rows:
        blob = " ".join(row.cells)
        if re.search(rf"\b{re.escape(wanted)}\b", blob.upper()):
            return Confirmation(ticker=wanted, cik=cik, citation=blob.strip(), accession=accession)
    return None
