"""What a cover page says about the registrant's own registered securities.

Every Exchange Act annual report opens with two declarations:

    Securities registered pursuant to Section 12(b) of the Act: ...
    Securities registered pursuant to Section 12(g) of the Act: ...

When both read **None**, that is not the absence of evidence — it is the
registrant stating it has no registered class, and therefore no ticker. The
resolver currently records such a case as ``not_established``, which is
indistinguishable from *"we could not parse it"*. Measured 2026-09-06 across a
live run, **55.6% of attempts** land in that bucket, so the difference between
"not found" and "never existed" is most of the remaining question.

Two registrants read in full show what is in there:

* ``AMERICAN RESTAURANT GROUP HOLDINGS`` — 12(b) *None*, 12(g) *None*. It files
  because it has registered **public debt**. The words "symbol", "traded" and
  "Nasdaq" appear zero times in 99,766 characters.
* ``FIDELITY LEASING INCOME FUND III LP`` — 12(b) *Not applicable*, 12(g)
  *Limited Partnership Interests*. A partnership, not a listed equity.

**The subtlety that makes a naive reading wrong.** Before 2006 the Nasdaq
National Market was not an "exchange" for Section 12(b) purposes, and its
companies registered under **12(g)**. So *12(b): None* on a 1998 filing does
**not** mean the stock did not trade — it may have traded on Nasdaq all along.
Only *both* sections reading None supports the conclusion that no class was
registered at all, and this module refuses to go further than that.

That is why :class:`RegisteredClasses` has four verdicts and not two. The
useful one for scoping a denominator is :attr:`RegisteredClasses.NONE_AT_ALL`;
:attr:`TWELVE_G_ONLY` is explicitly *inconclusive* rather than negative, and
treating it as negative would delete Nasdaq's entire pre-2006 population.
"""

from __future__ import annotations

import re
from enum import StrEnum

__all__ = ["RegisteredClasses", "read_registered_classes"]


class RegisteredClasses(StrEnum):
    """What the cover page establishes about registered securities."""

    #: 12(b) names a class. The registrant had an exchange-listed security.
    EXCHANGE_LISTED = "exchange_listed"
    #: 12(b) None, 12(g) names a class. **Inconclusive**: pre-2006 Nasdaq
    #: registered under 12(g), so this includes traded and untraded alike.
    TWELVE_G_ONLY = "twelve_g_only"
    #: Both None. The registrant states it has no registered class, so there is
    #: no ticker to find — a debt-only issuer, or a partnership.
    NONE_AT_ALL = "none_at_all"
    #: The cover page was not found or not parseable. Says nothing either way.
    UNDETERMINED = "undetermined"


#: Tolerates "Section 12(b)" and "Section 12 (b)", both of which occur.
_HEADER = r"Securities\s+registered\s+(?:pursuant\s+to\s+)?Section\s*12\s*\(\s*{}\s*\)"
_TWELVE_B = re.compile(_HEADER.format("b"), re.I)
_TWELVE_G = re.compile(_HEADER.format("g"), re.I)

#: Where the 12(g) declaration stops. The checkbox paragraph always follows.
_END = re.compile(
    r"Indicate\s+by\s+(?:a\s+)?check\s*(?:\s|-)?mark|"
    r"Check\s+whether\s+the\s+(?:issuer|registrant)|"
    r"Securities\s+Act\s+of\s+1933",
    re.I,
)

#: Column captions and rules that carry no content and must not be mistaken for
#: a class name. Order matters: the longest captions are removed first.
_FURNITURE = (
    re.compile(r"Title\s+of\s+each\s+class(?:\s+of\s+securities)?", re.I),
    re.compile(
        r"Name\s+of\s+each\s+exchange\s+on\s+which\s+(?:each\s+class\s+)?"
        r"(?:is\s+to\s+be\s+)?registered",
        re.I,
    ),
    re.compile(r"Trading\s+Symbol\(?s?\)?", re.I),
    re.compile(r"Title\s+of\s+Class", re.I),
    re.compile(r"of\s+the\s+Act\s*:?", re.I),
    re.compile("[-_=.\u2014\u2013|:;,*]+"),
)

#: What an empty declaration says. ``NIL`` and ``N/A`` both occur, and so does
#: the two-word ``Not applicable`` -- which is why these are **removed as
#: phrases** rather than matched word by word. Splitting on whitespace first
#: turned "Not applicable" into two unrecognised words and read a filing that
#: registers nothing as exchange-listed.
_EMPTY_PHRASE = re.compile(r"\b(?:none|not\s+applicable|not\s+available|n\s*/\s*a|nil)\b", re.I)


def _is_empty(block: str) -> bool:
    """True when a declaration block names no security at all.

    Everything that carries no meaning is deleted -- captions, rules, and the
    ways a filing writes "nothing" -- and the block is empty if nothing is
    left. Asking what *remains* is safer than enumerating what may appear.
    """
    text = block
    for pattern in _FURNITURE:
        text = pattern.sub(" ", text)
    text = _EMPTY_PHRASE.sub(" ", text)
    return not re.sub(r"[\s./]+", "", text)


def read_registered_classes(text: str) -> RegisteredClasses:
    """Read the two declarations off a cover page.

    ``text`` is the stripped filing text. Only the region between the 12(b)
    header and the checkbox paragraph is considered: a 10-K names exchanges and
    classes throughout, and reading the whole document would find a class in
    every filing that has ever discussed one.
    """
    start_b = _TWELVE_B.search(text)
    if start_b is None:
        return RegisteredClasses.UNDETERMINED
    start_g = _TWELVE_G.search(text, start_b.end())
    if start_g is None:
        return RegisteredClasses.UNDETERMINED

    block_b = text[start_b.end() : start_g.start()]
    end = _END.search(text, start_g.end())
    # Without the checkbox paragraph the 12(g) block has no end, and taking the
    # rest of the document would read the whole filing as a class name.
    if end is None:
        return RegisteredClasses.UNDETERMINED
    block_g = text[start_g.end() : end.start()]

    if not _is_empty(block_b):
        return RegisteredClasses.EXCHANGE_LISTED
    if not _is_empty(block_g):
        return RegisteredClasses.TWELVE_G_ONLY
    return RegisteredClasses.NONE_AT_ALL
