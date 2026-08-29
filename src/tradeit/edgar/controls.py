"""The 30-security control fixture, and its verification state.

**No CIK in this file is populated, and that is the point.** ``sec.gov`` is
unreachable from the build environment, so no mapping here has been checked
against a primary source. Writing plausible CIKs from memory would be
fabrication -- the specific failure the whole identity design exists to prevent
-- so every entry sits at ``MappingStatus.UNRESOLVED`` with the exact evidence
needed to lift it recorded beside it.

Milestone 0b is complete when
:func:`tradeit.edgar.control_evidence.controls_awaiting_manual_verification`
returns an empty tuple -- all thirty at ``MANUAL_VERIFIED``, which is the
milestone's own wording. It does not, today. That is a **stricter** question than
:func:`tradeit.edgar.control_evidence.unresolved_controls`, which asks only
whether a control's identity is established at all and which ``RESOLVED``
satisfies; the two counts are reported separately and are not interchangeable.

Both checks deliberately live in the evidence layer rather than here: what a
control's identity *is* comes from
``docs/research/control_identity_evidence.json``, and a gate that reads only the
placeholder below would report every control outstanding forever.

Tickers, names and years below are **UNVERIFIED recollection** used to *seek*
primary evidence, never to stand in for it. A control that cannot be confirmed
is replaced before any vendor data is seen, never after.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tradeit.edgar.identity import MappingStatus, SecurityMapping

__all__ = ["CONTROL_UNIVERSE", "ControlSecurity"]


@dataclass(frozen=True, slots=True)
class ControlSecurity:
    """One control, and what its absence would prove about a vendor."""

    ticker: str
    name: str
    control_class: str
    #: The lifecycle event to confirm against EDGAR.
    expected_event: str
    #: Recollected, UNVERIFIED. Used to search, never to assert.
    expected_year: int | None
    proves: str
    mapping: SecurityMapping = field(
        default_factory=lambda: SecurityMapping(
            cik=None,
            ticker=None,
            status=MappingStatus.UNRESOLVED,
            note="not yet checked against EDGAR",
        )
    )
    #: **Where to look for a filing that would establish this control's
    #: identity.** Design intent, like ``proves`` and ``expected_event``: it is
    #: UNVERIFIED, no gate reads it, and it is never evidence of anything. It
    #: records what a searcher should expect to find, and it does not record
    #: what was actually found -- that lives in the evidence file, and storing a
    #: finding here would eventually let an expectation be read as one.
    #:
    #: A usable route names a document class that can join **registrant -> its
    #: own security -> venue -> symbol**. Three kinds of route cannot, and all
    #: three were shipped here until every control had been verified and the
    #: mismatch became visible:
    #:
    #: * a **reference file** such as ``company_tickers.json`` -- a dated
    #:   primary source, but not a filing anyone read, which is a categorical
    #:   gap from ``MANUAL_VERIFIED`` rather than a matter of confidence;
    #: * a **lifecycle instrument** -- Form 25, Form 15, an 8-K, a DEF 14A --
    #:   which evidences a corporate action rather than an identity;
    #: * a **name search**, which establishes which CIK to read and no part of
    #:   the mapping.
    #:
    #: The era matters and is worth recording per control: a Section 12(b)
    #: table only carries a ``Trading Symbol(s)`` column in recent filings, so
    #: for older ones the narrative clause is the construction to look for.
    verification_route: str = (
        "the registrant's own filing joining issuer, security, venue and symbol; a name "
        "search only narrows which CIK to read"
    )
    #: **How many issuer identities this control must SETTLE, not how many
    #: existed.** A value of 2 says "this control cannot be declared fully
    #: adjudicated until the question of a second issuer identity has been
    #: explicitly settled" -- it does NOT say a second issuer definitely
    #: existed, and nothing may read it as evidence that one did.
    #:
    #: The obligation is discharged either way: by recording enough
    #: independently evidenced mappings, or by recording a cited adjudication
    #: that the remaining issuer question was investigated and no further
    #: issuer was established. Both are findings; neither is assumed.
    #:
    #: It lives here rather than in the evidence file because it is a property
    #: of why this control was *chosen* -- design intent, like ``proves`` --
    #: and the evidence file holds only what was found. An expectation stored
    #: beside findings would eventually be read as one.
    required_issuer_investigations: int = 1


def _c(
    ticker: str,
    name: str,
    control_class: str,
    expected_event: str,
    expected_year: int | None,
    proves: str,
    route: str = (
        "the registrant's own filing joining issuer, security, venue and symbol; a name "
        "search only narrows which CIK to read"
    ),
    required_issuer_investigations: int = 1,
) -> ControlSecurity:
    return ControlSecurity(
        ticker=ticker,
        name=name,
        control_class=control_class,
        expected_event=expected_event,
        expected_year=expected_year,
        proves=proves,
        verification_route=route,
        required_issuer_investigations=required_issuer_investigations,
    )


CONTROL_UNIVERSE: tuple[ControlSecurity, ...] = (
    _c(
        "AAPL",
        "Apple Inc.",
        "survivor + large splits",
        "continuous listing",
        None,
        "baseline; four splits including 7:1 and 4:1",
        "recent Form 10-K cover page: the Section 12(b) Trading Symbol(s) column",
    ),
    _c(
        "MSFT",
        "Microsoft Corporation",
        "survivor",
        "continuous listing",
        None,
        "splits 1998, 1999, 2003",
        "recent Form 10-K cover page: the Section 12(b) Trading Symbol(s) column",
    ),
    _c(
        "CSCO",
        "Cisco Systems, Inc.",
        "survivor through collapse",
        "continuous listing",
        None,
        "a -85% drawdown that is not a delisting",
        "recent Form 10-K cover page: the Section 12(b) Trading Symbol(s) column",
    ),
    _c(
        "AMZN",
        "Amazon.com, Inc.",
        "survivor through collapse",
        "continuous listing",
        None,
        "1997 IPO, -90% drawdown, 20:1 split 2022",
        "recent Form 10-K cover page: the Section 12(b) Trading Symbol(s) column",
    ),
    _c(
        "SPY",
        "SPDR S&P 500 ETF Trust",
        "long-lived ETF",
        "continuous listing",
        None,
        "non-equity control; dividends without splits",
        "the trust's own Form 485BPOS under its own CIK; confirm the trust, not the sponsor",
    ),
    _c(
        "QQQ",
        "Invesco QQQ Trust",
        "ETF born in the bubble",
        "listing 1999; 2:1 split 2000",
        1999,
        "an ETF that began inside the window",
        "the trust's own Form 485BPOS under its own CIK; the sponsor changed over time",
    ),
    _c(
        "IPET",
        "Pets.com, Inc.",
        "short-lived failure",
        "IPO then wind-up",
        2000,
        "~9 months of sessions. The sharpest single test in the fixture",
        "its IPO prospectus (Form 424B); it died long before the trading-symbol column",
    ),
    _c(
        "ETYS",
        "eToys, Inc.",
        "short-lived failure",
        "IPO 1999, Chapter 11",
        2001,
        "IPO to bankruptcy inside the window",
        "its IPO prospectus (Form 424B); no annual report of its own carries a symbol table",
    ),
    _c(
        "WBVN",
        "Webvan Group, Inc.",
        "short-lived failure",
        "IPO 1999, Chapter 11",
        2001,
        "large raise, total loss",
        "its IPO prospectus (Form 424B); short-lived, so a prospectus is the likeliest join",
    ),
    _c(
        "TGLO",
        "theglobe.com, inc.",
        "collapse, survived as shell",
        "collapse",
        2001,
        "distinguishes bankruptcy from shell survival",
        "an annual report of its Nasdaq era: the 'quoted on ... under the symbol' narrative",
    ),
    _c(
        "KOOP",
        "drkoop.com, Inc.",
        "short-lived failure",
        "going concern then absorption",
        2001,
        "a name no vendor markets",
        "its IPO prospectus (Form 424B); short-lived, so a prospectus is the likeliest join",
    ),
    _c(
        "MPPP",
        "MP3.com, Inc.",
        "acquisition of a failing company",
        "acquired",
        2001,
        "acquisition is not the same event as failure",
        "its IPO prospectus (Form 424B); acquired before a symbol table would have existed",
    ),
    _c(
        "ENE",
        "Enron Corp.",
        "large-cap collapse",
        "Chapter 11",
        2001,
        "large-cap disappearance",
        "each registrant's own filing; the predecessor's submission carries multiple filer "
        "blocks, so attribution comes from the text and never from the header",
    ),
    _c(
        "WCOM",
        "WorldCom, Inc.",
        "large-cap collapse then reorganisation",
        "Chapter 11",
        2002,
        "emerges as MCI — a new identity, not a continuation",
        "the registrant's own annual report; a 2001 filing is Form 10-K405, not 10-K",
    ),
    _c(
        "EXDS",
        "Exodus Communications, Inc.",
        "infrastructure failure",
        "Chapter 11",
        2001,
        "the infrastructure cohort",
        "the registrant's own Form 10-K narrative; a 2001 12(b) table has no symbol column",
    ),
    _c(
        "PSIX",
        "PSINet Inc.",
        "infrastructure failure",
        "Chapter 11",
        2001,
        "the infrastructure cohort",
        "the registrant's own Form 10-K narrative; a 2000 12(b) table has no symbol column",
    ),
    _c(
        "GCTY",
        "GeoCities",
        "peak acquisition",
        "acquired by Yahoo",
        1999,
        "the series must END, not continue into the acquirer",
        "its own Form 10-K narrative, filed while still independent of the acquirer",
    ),
    _c(
        "BCST",
        "broadcast.com inc.",
        "peak acquisition",
        "acquired by Yahoo",
        1999,
        "same; two acquisitions by one acquirer in one year",
        "its IPO prospectus (Form 424B); acquired before it filed an annual report",
    ),
    _c(
        "CPQ",
        "Compaq Computer Corporation",
        "merger, identity change",
        "merged into HP",
        2002,
        "large-cap absorption",
        "Compaq's own Form 10-K narrative, filed before the merger closed",
    ),
    _c(
        "BEL",
        "Bell Atlantic Corporation",
        "rename with continuity",
        "became Verizon",
        2000,
        "ticker change, ONE economic security",
        "a filing under the continuing CIK; formerNames explains the rename and establishes no "
        "symbol",
    ),
    _c(
        "BBBY",
        "Bed Bath & Beyond Inc.",
        "ticker reuse — proven",
        "delisted, ticker reused",
        2023,
        "the exact case full-01 failed on. Non-negotiable",
        "two distinct CIKs; each registrant's own Form 10-K cover-page 12(b) table",
        required_issuer_investigations=2,
    ),
    _c(
        "GM",
        "General Motors (Corporation, then Company)",
        "ticker reuse, ~1yr gap",
        "bankruptcy 2009, new issuer IPO 2010",
        2009,
        "short enough that a splice looks plausible — the strictest reuse case",
        "two distinct CIKs; each registrant's own annual report, never one filing for both",
        required_issuer_investigations=2,
    ),
    _c(
        "AOL",
        "America Online / AOL Inc.",
        "ticker reuse, ~8yr gap",
        "two distinct issuers",
        2001,
        "same ticker, unrelated registrants",
        "two distinct CIKs; each registrant's own annual report -- the names are close, so the "
        "CIK is the only discriminator",
        required_issuer_investigations=2,
    ),
    _c(
        "JDSU",
        "JDS Uniphase Corporation",
        "reverse split",
        "1:8 reverse split",
        2006,
        "the largest reverse factor in the fixture",
        "its own Form 10-K narrative; a Nasdaq class of that era is registered under 12(g), so no "
        "12(b) row exists to find",
    ),
    _c(
        "PCLN",
        "priceline.com Incorporated",
        "reverse split, survivor",
        "1:6 reverse split",
        2003,
        "unadjusted chart looks like a catastrophe; adjusted does not. "
        "The most valuable single control",
        "its own Form 10-K narrative; a 2003 12(b) table predates the symbol column",
    ),
    _c(
        "QCOM",
        "QUALCOMM Incorporated",
        "large forward split",
        "4:1 split",
        1999,
        "a large forward split at the peak",
        "the annual report of its era, which is Form 10-K405 and not 10-K; the narrative carries "
        "the symbol",
    ),
    _c(
        "LEH",
        "Lehman Brothers Holdings Inc.",
        "financial-crisis failure",
        "Chapter 11",
        2008,
        "the crisis cohort; Form 25 era, so electronically evidenced",
        "a prospectus or annual report of its era joining registrant, security, venue and symbol",
    ),
    _c(
        "CC",
        "Circuit City Stores, Inc.",
        "crisis liquidation",
        "liquidation",
        2009,
        "a non-financial crisis failure",
        "its own Form 10-K narrative; a 2008 12(b) row names a class and an exchange but no symbol",
    ),
    _c(
        "FRC",
        "First Republic Bank",
        "recent delisting",
        "failure and delisting",
        2023,
        "the recent end is maintained, not just the archive",
        "NOT EDGAR: a bank with no holding company files Exchange Act reports with the FDIC. Its "
        "FDIC-filed Form 10-K cover page, with FDIC/FFIEC records for the issuer identifier",
    ),
    _c(
        "RDDT",
        "Reddit, Inc.",
        "recent IPO",
        "IPO",
        2024,
        "the young end of the universe",
        "its first post-IPO Form 10-K cover page: the Section 12(b) Trading Symbol(s) column",
    ),
)


# Milestone 0b's remaining work is reported by
# `tradeit.edgar.control_evidence.controls_awaiting_manual_verification`, and the
# weaker "is this control's identity known at all" question by
# `unresolved_controls` in the same module. Neither is answered from here.
#
# Two functions used to live at the bottom of this file -- `unverified()` and
# `verification_table()` -- and both read `ControlSecurity.mapping` as though it
# were the control's state. It never is. That field is a placeholder pinned at
# UNRESOLVED so a remembered CIK cannot be written into source, and the identity
# actually established for a control is recorded in
# docs/research/control_identity_evidence.json.
#
# So `unverified()` returned all thirty regardless of what had been verified, and
# would have gone on returning thirty after the last control was confirmed. The
# fix is not a cleverer read of this file: it is that the completion gate belongs
# to the layer that joins this fixture to that evidence, so there is one answer
# to "is this control verified" rather than two that agree by coincidence.
