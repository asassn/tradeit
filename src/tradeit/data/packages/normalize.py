"""Turning cell text into typed values, and saying what it changed.

Normalization is the stage where most silent corruption happens in data
pipelines, because every coercion is a small guess and small guesses do not feel
like decisions. Three rules keep it honest here.

**Guessing is not allowed where the guess could be wrong.** ``03/04/2021`` is
March 4th in one country and April 3rd in another, and a file usually contains
both plausible readings across its rows, so no amount of sampling settles it.
The importer therefore refuses slash-separated numeric dates outright and tells
the operator to declare ``date_format`` in the manifest. That is more friction
than a heuristic and it is the difference between an import that is right and an
import that is right most of the time.

**Every change is recorded.** Stripping a currency symbol, dropping thousands
separators, mapping ``N/A`` to null — each returns a
:class:`~tradeit.data.packages.stages.Correction` naming the rule and the
reason. A normalized record that carries no corrections provably changed
nothing.

**Failure is a quarantine, not an exception that unwinds the import.** Coercion
raises :class:`CoercionError`, the importer catches it per row, and the row goes
to quarantine with the field and the offending text in the message. One bad cell
in a ten-million-row export must not cost the other 9,999,999.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from tradeit.data.packages.stages import Correction
from tradeit.errors import DataError

#: Tokens vendors use for "no value". Compared case-folded. ``0`` is deliberately
#: absent: a volume of zero is a fact about a session, not a missing reading, and
#: conflating them is how a halted stock becomes a gap.
DEFAULT_NULL_TOKENS: frozenset[str] = frozenset(
    {"", "-", "--", "n/a", "na", "nan", "nat", "null", "none", "nil", "#n/a", "(null)"}
)

#: Date layouts that cannot be read two ways. Anything ambiguous must be
#: declared in the manifest instead of inferred.
UNAMBIGUOUS_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y%m%d",
    "%d-%b-%Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%d-%B-%Y",
    "%B %d, %Y",
)

_TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1"})
_FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0"})


class CoercionError(DataError):
    """One cell could not be turned into the type its column promises."""


@dataclass(frozen=True, slots=True)
class NormalizationPolicy:
    """What the importer is permitted to fix without being told each time.

    Everything here defaults to the conservative setting. An operator whose
    vendor writes ``1.234,56`` turns on ``decimal_comma`` in the manifest and
    the corrections then say so on every affected row, which is a different
    thing from the importer having quietly worked it out.
    """

    #: Extra ``strptime`` patterns, in order. Declaring ``%m/%d/%Y`` here is how
    #: an American export becomes importable; without it, its dates quarantine.
    date_formats: tuple[str, ...] = ()
    #: IANA zone applied to naive timestamps. Absent, naive timestamps fail —
    #: a timestamp with no zone has no instant, and picking UTC for it silently
    #: shifts every US session by hours.
    timezone: str | None = None
    #: ``1.234,56`` style. Off by default because ``1,234`` then means 1.234.
    decimal_comma: bool = False
    #: Characters removed from numbers before parsing, with a correction each.
    strip_from_numbers: tuple[str, ...] = ("$", " ", "\u00a0", "\u202f", "_")
    #: Accept ``1,234,567``. Independent of ``decimal_comma`` — enabling both is
    #: rejected, since a comma cannot be both a group and a decimal mark.
    thousands_comma: bool = True
    null_tokens: frozenset[str] = field(default=DEFAULT_NULL_TOKENS)
    #: Uppercase ticker-like strings. Off by default: ``BRK.b`` and ``BRK.B``
    #: are the same security to a human and different keys to a dict, but a
    #: vendor that distinguishes case is a vendor we should not silently flatten.
    upper_symbols: bool = False

    def __post_init__(self) -> None:
        if self.decimal_comma and self.thousands_comma:
            raise DataError(
                "decimal_comma and thousands_comma cannot both be on: a comma "
                "cannot be both the decimal mark and the group separator. Turn "
                "off thousands_comma for European-format exports."
            )
        for pattern in self.date_formats:
            try:
                dt.datetime.strptime("2021-03-04", pattern)  # noqa: DTZ007 - probe only
            except ValueError:
                continue
            except Exception as error:  # pragma: no cover - defensive
                raise DataError(f"invalid date format {pattern!r}: {error}") from error

    @property
    def zone(self) -> ZoneInfo | None:
        return ZoneInfo(self.timezone) if self.timezone else None


def is_null(text: str | None, policy: NormalizationPolicy) -> bool:
    return text is None or text.strip().casefold() in policy.null_tokens


def coerce_date(text: str, field_name: str, policy: NormalizationPolicy) -> dt.date:
    """Parse a date, refusing to guess between DD/MM and MM/DD."""
    value = text.strip()
    for pattern in (*policy.date_formats, *UNAMBIGUOUS_DATE_FORMATS):
        try:
            return dt.datetime.strptime(value, pattern).date()  # noqa: DTZ007 - date only
        except ValueError:
            continue
    if _looks_ambiguous(value):
        raise CoercionError(
            f"{field_name}={value!r} is a slash-separated numeric date, which is "
            "March 4th in some countries and April 3rd in others. The importer "
            "will not guess. Declare the layout in the manifest, e.g. "
            'date_format = "%m/%d/%Y", and re-run.'
        )
    raise CoercionError(
        f"{field_name}={value!r} is not a date the importer recognises. "
        f"Unambiguous layouts: {', '.join(UNAMBIGUOUS_DATE_FORMATS)}. Anything "
        "else must be declared as date_format in the manifest."
    )


def _looks_ambiguous(value: str) -> bool:
    parts = value.replace("-", "/").split("/")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return False
    return len(parts[0]) <= 2


def coerce_datetime(
    text: str, field_name: str, policy: NormalizationPolicy
) -> tuple[dt.datetime, tuple[Correction, ...]]:
    """Parse a timestamp to an aware UTC instant.

    A naive timestamp is not an instant. Rather than assume UTC — which would
    silently move every US market close by four or five hours — a naive value
    is localised with the package's declared timezone, and the localisation is
    recorded as a correction so the assumption is visible on the row. With no
    declared timezone the row quarantines.
    """
    value = text.strip().replace("Z", "+00:00")
    parsed: dt.datetime | None = None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        for pattern in (*policy.date_formats, *UNAMBIGUOUS_DATE_FORMATS):
            try:
                parsed = dt.datetime.strptime(value, pattern)  # noqa: DTZ007 - localised below
                break
            except ValueError:
                continue
    if parsed is None:
        raise CoercionError(
            f"{field_name}={text!r} is not a timestamp. ISO-8601 "
            "(2021-03-04T20:15:00+00:00) is always accepted."
        )
    if parsed.tzinfo is not None:
        utc = parsed.astimezone(dt.UTC)
        note: tuple[Correction, ...] = ()
        if utc != parsed:
            note = (
                Correction(
                    field=field_name,
                    raw=text,
                    corrected=utc.isoformat(),
                    rule="to_utc",
                    reason="converted from its declared offset to UTC for storage",
                ),
            )
        return utc, note

    zone = policy.zone
    if zone is None:
        raise CoercionError(
            f"{field_name}={text!r} has no timezone and the package declares "
            "none. A timestamp without a zone has no instant; add timezone to "
            "the manifest or export timestamps with offsets."
        )
    localised = parsed.replace(tzinfo=zone).astimezone(dt.UTC)
    return localised, (
        Correction(
            field=field_name,
            raw=text,
            corrected=localised.isoformat(),
            rule="localise_naive",
            reason=f"no offset in the file; applied the package timezone {policy.timezone}",
        ),
    )


def coerce_decimal(
    text: str, field_name: str, policy: NormalizationPolicy
) -> tuple[Decimal, tuple[Correction, ...]]:
    """Parse a number to Decimal, never through float.

    ``float("0.1")`` is not 0.1, and a price series built from floats
    accumulates error that later looks like a market microstructure effect. See
    ``tradeit.core.money``.
    """
    cleaned = text.strip()
    corrections: list[Correction] = []
    for token in policy.strip_from_numbers:
        if token in cleaned:
            cleaned = cleaned.replace(token, "")
            corrections.append(
                Correction(
                    field=field_name,
                    raw=text,
                    corrected=cleaned,
                    rule="strip_number_noise",
                    reason=f"removed {token!r}, which is formatting rather than value",
                )
            )
    if policy.decimal_comma:
        cleaned = cleaned.replace(".", "").replace(",", ".")
        corrections.append(
            Correction(
                field=field_name,
                raw=text,
                corrected=cleaned,
                rule="decimal_comma",
                reason="package declares comma as the decimal mark",
            )
        )
    elif policy.thousands_comma and "," in cleaned:
        cleaned = cleaned.replace(",", "")
        corrections.append(
            Correction(
                field=field_name,
                raw=text,
                corrected=cleaned,
                rule="strip_thousands",
                reason="removed group separators",
            )
        )
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
        corrections.append(
            Correction(
                field=field_name,
                raw=text,
                corrected=cleaned,
                rule="accounting_negative",
                reason="parenthesised value read as negative, per accounting convention",
            )
        )
    if cleaned.endswith("%"):
        raise CoercionError(
            f"{field_name}={text!r} carries a percent sign. The importer will "
            "not decide whether 12.5% means 12.5 or 0.125; export the number "
            "without the sign in whichever unit the column contract specifies."
        )
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError) as error:
        raise CoercionError(f"{field_name}={text!r} is not a number") from error
    if not value.is_finite():
        raise CoercionError(
            f"{field_name}={text!r} parses to {value}, which is not a finite "
            "quantity and cannot be a price, a volume or a fundamental."
        )
    return value, tuple(corrections)


def coerce_int(
    text: str, field_name: str, policy: NormalizationPolicy
) -> tuple[int, tuple[Correction, ...]]:
    """Parse an integer, accepting ``1234.0`` but not ``1234.5``.

    Vendors routinely export integer columns through a float pipeline, so
    ``instrument_id`` arrives as ``4211.0``. Accepting that is safe; accepting
    ``4211.5`` would mean inventing a row identity.
    """
    value, corrections = coerce_decimal(text, field_name, policy)
    if value != value.to_integral_value():
        raise CoercionError(
            f"{field_name}={text!r} has a fractional part but the column "
            "contract is an integer; rounding it would change what row this is."
        )
    integral = int(value)
    if str(integral) != text.strip() and not corrections:
        corrections = (
            Correction(
                field=field_name,
                raw=text,
                corrected=str(integral),
                rule="integral_from_decimal",
                reason="value was written with a decimal point but has no fractional part",
            ),
        )
    return integral, corrections


def coerce_bool(text: str, field_name: str) -> bool:
    token = text.strip().casefold()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise CoercionError(
        f"{field_name}={text!r} is not a boolean. Accepted: "
        f"{sorted(_TRUE_TOKENS)} / {sorted(_FALSE_TOKENS)}."
    )


def coerce_string(
    text: str, field_name: str, policy: NormalizationPolicy
) -> tuple[str, tuple[Correction, ...]]:
    value = text.strip()
    corrections: list[Correction] = []
    if value != text:
        corrections.append(
            Correction(
                field=field_name,
                raw=text,
                corrected=value,
                rule="trim",
                reason="surrounding whitespace removed",
            )
        )
    if policy.upper_symbols and field_name in {"ticker", "symbol"} and value != value.upper():
        upper = value.upper()
        corrections.append(
            Correction(
                field=field_name,
                raw=value,
                corrected=upper,
                rule="upper_symbol",
                reason="package declares symbols are case-insensitive",
            )
        )
        value = upper
    if not value:
        raise CoercionError(f"{field_name} is empty after trimming")
    return value, tuple(corrections)


def coerce(
    kind: str,
    text: str,
    field_name: str,
    policy: NormalizationPolicy,
) -> tuple[object, tuple[Correction, ...]]:
    """Dispatch on the column contract's declared kind."""
    if kind == "date":
        return coerce_date(text, field_name, policy), ()
    if kind == "datetime":
        return coerce_datetime(text, field_name, policy)
    if kind == "decimal":
        return coerce_decimal(text, field_name, policy)
    if kind == "int":
        return coerce_int(text, field_name, policy)
    if kind == "bool":
        return coerce_bool(text, field_name), ()
    if kind == "string":
        return coerce_string(text, field_name, policy)
    raise DataError(
        f"column {field_name!r} declares kind {kind!r}, which the importer does "
        "not know how to read. Valid kinds: date, datetime, decimal, int, bool, string."
    )


__all__ = [
    "DEFAULT_NULL_TOKENS",
    "UNAMBIGUOUS_DATE_FORMATS",
    "CoercionError",
    "NormalizationPolicy",
    "coerce",
    "coerce_bool",
    "coerce_date",
    "coerce_datetime",
    "coerce_decimal",
    "coerce_int",
    "coerce_string",
    "is_null",
]
