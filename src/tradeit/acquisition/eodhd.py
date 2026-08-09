"""EODHD adapter — registered, wired, and deliberately unfinished.

Its purpose is to prove the seam rather than to fetch anything. Provider
switching is a requirement, and the only honest way to demonstrate that a
pluggable interface is actually pluggable is to plug a second thing into it and
see what breaks. Writing this adapter is what confirmed that
:class:`~tradeit.acquisition.base.AcquisitionProvider` needs no Tiingo-shaped
assumptions: EODHD splits corporate actions across dedicated endpoints rather
than carrying them inline on price rows, and the interface accommodates that
without change because a request names a *dataset*, not an endpoint.

**Why it does not fetch.** Completing it would mean guessing this vendor's URL
shapes, parameter names, response fields and error semantics from memory, and
then writing tests against fixtures built from the same guesses — which would
produce a green suite that proves nothing and an adapter that fails on first
contact with the real API. The environment this was written in cannot reach
`eodhd.com` to check. So the structure is here, the gaps are named, and the
adapter refuses clearly rather than failing obscurely.

**To finish it**, with the API documentation open:

1. Fill in :data:`ENDPOINTS` with the real paths.
2. Implement :meth:`EodhdAcquisition._url` for each dataset.
3. Implement :meth:`EodhdAcquisition._rows` for the response shapes.
4. Add a fixture per endpoint under ``tests/fixtures/eodhd/`` captured from a
   real response with the key stripped, and extend the acquisition tests to
   run against them the same way the Tiingo tests do.
5. Add ``SPLITS`` and ``DIVIDENDS`` as separate
   :class:`~tradeit.acquisition.base.AcquisitionDataset` members if the
   dedicated endpoints are used — the runner already handles a provider whose
   datasets do not map one-to-one onto requests.

Nothing in the package format, the manifest, the normalizer or the validator
needs to change for any of that.
"""

from __future__ import annotations

from typing import Any

from tradeit.acquisition.base import (
    AcquisitionDataset,
    FetchOutcome,
    FetchRequest,
    FetchStatus,
    register,
)
from tradeit.data.packages.spec import DatasetKind

#: Endpoint paths, to be filled in against the vendor's documentation. Empty
#: rather than guessed: a plausible-looking wrong path produces a 404 that
#: reads like a missing symbol.
ENDPOINTS: dict[AcquisitionDataset, str] = {}

NOT_IMPLEMENTED_MESSAGE = (
    "the EODHD adapter is a registered stub, not a working provider. Its "
    "structure exists to keep provider switching honest — the acquisition "
    "interface was designed against two vendors rather than one — but its "
    "endpoints were deliberately left unfilled rather than guessed from memory, "
    "because a guessed URL produces a 404 that reads like a missing symbol. "
    "See the module docstring in tradeit/acquisition/eodhd.py for the five "
    "steps to finish it. Use --provider tiingo today."
)


@register
class EodhdAcquisition:
    """Structure only. See the module docstring."""

    name = "eodhd"
    credential_env = "EODHD_API_KEY"

    #: What the vendor is understood to offer, so the runner can report what
    #: finishing this adapter would unlock. **NEEDS VERIFICATION** — this list
    #: is from public description, not from a trial.
    supported: tuple[AcquisitionDataset, ...] = (
        AcquisitionDataset.SYMBOL_META,
        AcquisitionDataset.DAILY_PRICES,
        AcquisitionDataset.CORPORATE_ACTIONS,
    )
    produces: tuple[DatasetKind, ...] = (
        DatasetKind.INSTRUMENTS,
        DatasetKind.SYMBOL_MAPPINGS,
        DatasetKind.DAILY_BARS,
        DatasetKind.SPLITS,
        DatasetKind.DIVIDENDS,
        DatasetKind.DELISTINGS,
    )
    rate_limit_per_minute = 60

    implemented = False

    def __init__(self, **_: Any) -> None:
        return None

    def has_credential(self) -> bool:
        return False

    def fetch(self, request: FetchRequest) -> FetchOutcome:
        return FetchOutcome(
            request=request, status=FetchStatus.REJECTED, error=NOT_IMPLEMENTED_MESSAGE
        )

    def limitations(self) -> list[str]:
        return [NOT_IMPLEMENTED_MESSAGE]

    def missing_datasets(self) -> list[DatasetKind]:
        return list(DatasetKind)


__all__ = ["ENDPOINTS", "NOT_IMPLEMENTED_MESSAGE", "EodhdAcquisition"]
