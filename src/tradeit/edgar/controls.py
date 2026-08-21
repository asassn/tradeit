"""The 30-security control fixture, and its verification state.

**No CIK in this file is populated, and that is the point.** ``sec.gov`` is
unreachable from the build environment, so no mapping here has been checked
against a primary source. Writing plausible CIKs from memory would be
fabrication -- the specific failure the whole identity design exists to prevent
-- so every entry sits at ``MappingStatus.UNRESOLVED`` with the exact evidence
needed to lift it recorded beside it.

Milestone 0b is complete when
:func:`tradeit.edgar.control_evidence.outstanding_controls` returns an empty
tuple. It does not, today. That check deliberately lives in the evidence layer
rather than here: what a control's identity *is* comes from
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
    #: What would lift this to MANUAL_VERIFIED.
    verification_route: str = "EDGAR company search by name; confirm CIK, form, and date"


def _c(
    ticker: str,
    name: str,
    control_class: str,
    expected_event: str,
    expected_year: int | None,
    proves: str,
    route: str = "EDGAR company search by name; confirm CIK, form, and date",
) -> ControlSecurity:
    return ControlSecurity(
        ticker=ticker,
        name=name,
        control_class=control_class,
        expected_event=expected_event,
        expected_year=expected_year,
        proves=proves,
        verification_route=route,
    )


CONTROL_UNIVERSE: tuple[ControlSecurity, ...] = (
    _c(
        "AAPL",
        "Apple Inc.",
        "survivor + large splits",
        "continuous listing",
        None,
        "baseline; four splits including 7:1 and 4:1",
        "company_tickers.json — currently listed, mapping is direct",
    ),
    _c(
        "MSFT",
        "Microsoft Corporation",
        "survivor",
        "continuous listing",
        None,
        "splits 1998, 1999, 2003",
        "company_tickers.json — currently listed",
    ),
    _c(
        "CSCO",
        "Cisco Systems, Inc.",
        "survivor through collapse",
        "continuous listing",
        None,
        "a -85% drawdown that is not a delisting",
        "company_tickers.json — currently listed",
    ),
    _c(
        "AMZN",
        "Amazon.com, Inc.",
        "survivor through collapse",
        "continuous listing",
        None,
        "1997 IPO, -90% drawdown, 20:1 split 2022",
        "company_tickers.json — currently listed",
    ),
    _c(
        "SPY",
        "SPDR S&P 500 ETF Trust",
        "long-lived ETF",
        "continuous listing",
        None,
        "non-equity control; dividends without splits",
        "trust files under its own CIK; confirm the trust, not the sponsor",
    ),
    _c(
        "QQQ",
        "Invesco QQQ Trust",
        "ETF born in the bubble",
        "listing 1999; 2:1 split 2000",
        1999,
        "an ETF that began inside the window",
        "trust CIK; note the sponsor changed over time",
    ),
    _c(
        "IPET",
        "Pets.com, Inc.",
        "short-lived failure",
        "IPO then wind-up",
        2000,
        "~9 months of sessions. The sharpest single test in the fixture",
        "S-1/424B and a subsequent Form 15 or 8-K; Form 25 unlikely pre-2005",
    ),
    _c(
        "ETYS",
        "eToys, Inc.",
        "short-lived failure",
        "IPO 1999, Chapter 11",
        2001,
        "IPO to bankruptcy inside the window",
        "8-K text for the bankruptcy; Form 15 for deregistration",
    ),
    _c(
        "WBVN",
        "Webvan Group, Inc.",
        "short-lived failure",
        "IPO 1999, Chapter 11",
        2001,
        "large raise, total loss",
        "8-K text; Form 15",
    ),
    _c(
        "TGLO",
        "theglobe.com, inc.",
        "collapse, survived as shell",
        "collapse",
        2001,
        "distinguishes bankruptcy from shell survival",
        "continued filings after collapse are the evidence",
    ),
    _c(
        "KOOP",
        "drkoop.com, Inc.",
        "short-lived failure",
        "going concern then absorption",
        2001,
        "a name no vendor markets",
        "8-K/Form 15; may have been acquired rather than liquidated",
    ),
    _c(
        "MPPP",
        "MP3.com, Inc.",
        "acquisition of a failing company",
        "acquired",
        2001,
        "acquisition is not the same event as failure",
        "8-K completion text; acquirer's S-4",
    ),
    _c(
        "ENE",
        "Enron Corp.",
        "large-cap collapse",
        "Chapter 11",
        2001,
        "large-cap disappearance",
        "8-K text 2001; Form 15",
    ),
    _c(
        "WCOM",
        "WorldCom, Inc.",
        "large-cap collapse then reorganisation",
        "Chapter 11",
        2002,
        "emerges as MCI — a new identity, not a continuation",
        "8-K; successor registrant is a separate CIK",
    ),
    _c(
        "EXDS",
        "Exodus Communications, Inc.",
        "infrastructure failure",
        "Chapter 11",
        2001,
        "the infrastructure cohort",
        "",
    ),
    _c(
        "PSIX",
        "PSINet Inc.",
        "infrastructure failure",
        "Chapter 11",
        2001,
        "the infrastructure cohort",
        "",
    ),
    _c(
        "GCTY",
        "GeoCities",
        "peak acquisition",
        "acquired by Yahoo",
        1999,
        "the series must END, not continue into the acquirer",
        "acquirer S-4 plus target Form 15",
    ),
    _c(
        "BCST",
        "broadcast.com inc.",
        "peak acquisition",
        "acquired by Yahoo",
        1999,
        "same; two acquisitions by one acquirer in one year",
        "acquirer S-4 plus target Form 15",
    ),
    _c(
        "CPQ",
        "Compaq Computer Corporation",
        "merger, identity change",
        "merged into HP",
        2002,
        "large-cap absorption",
        "HP S-4; Compaq Form 15/25",
    ),
    _c(
        "BEL",
        "Bell Atlantic Corporation",
        "rename with continuity",
        "became Verizon",
        2000,
        "ticker change, ONE economic security",
        "same CIK continues under the new name; submissions formerNames",
    ),
    _c(
        "BBBY",
        "Bed Bath & Beyond Inc.",
        "ticker reuse — proven",
        "delisted, ticker reused",
        2023,
        "the exact case full-01 failed on. Non-negotiable",
        "Form 25 and Form 15 exist electronically; the later holder is a different CIK",
    ),
    _c(
        "GM",
        "General Motors (Corporation, then Company)",
        "ticker reuse, ~1yr gap",
        "bankruptcy 2009, new issuer IPO 2010",
        2009,
        "short enough that a splice looks plausible — the strictest reuse case",
        "two distinct CIKs; Motors Liquidation vs General Motors Company",
    ),
    _c(
        "AOL",
        "America Online / AOL Inc.",
        "ticker reuse, ~8yr gap",
        "two distinct issuers",
        2001,
        "same ticker, unrelated registrants",
        "two CIKs; the 2009 spin-off registered separately",
    ),
    _c(
        "JDSU",
        "JDS Uniphase Corporation",
        "reverse split",
        "1:8 reverse split",
        2006,
        "the largest reverse factor in the fixture",
        "8-K/DEF 14A for the reverse split",
    ),
    _c(
        "PCLN",
        "priceline.com Incorporated",
        "reverse split, survivor",
        "1:6 reverse split",
        2003,
        "unadjusted chart looks like a catastrophe; adjusted does not. "
        "The most valuable single control",
        "DEF 14A authorising the reverse split",
    ),
    _c(
        "QCOM",
        "QUALCOMM Incorporated",
        "large forward split",
        "4:1 split",
        1999,
        "a large forward split at the peak",
        "company_tickers.json — currently listed",
    ),
    _c(
        "LEH",
        "Lehman Brothers Holdings Inc.",
        "financial-crisis failure",
        "Chapter 11",
        2008,
        "the crisis cohort; Form 25 era, so electronically evidenced",
        "8-K item 1.03 (post-2004 numbering); Form 25",
    ),
    _c(
        "CC",
        "Circuit City Stores, Inc.",
        "crisis liquidation",
        "liquidation",
        2009,
        "a non-financial crisis failure",
        "8-K; Form 25; Form 15",
    ),
    _c(
        "FRC",
        "First Republic Bank",
        "recent delisting",
        "failure and delisting",
        2023,
        "the recent end is maintained, not just the archive",
        "Form 25-NSE expected; bank receivership complicates the 8-K trail",
    ),
    _c(
        "RDDT",
        "Reddit, Inc.",
        "recent IPO",
        "IPO",
        2024,
        "the young end of the universe",
        "S-1/424B4; company_tickers.json",
    ),
)


# Milestone 0b's remaining work is reported by
# `tradeit.edgar.control_evidence.outstanding_controls`, not from here.
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
