"""Credits and HTTP requests, counted separately because they are separate.

Twelve Data prices `/time_series` **per symbol**, so a request for
``AAPL,MSFT,NVDA`` is one HTTP call and three credits. Batching is therefore
worth doing — it cuts connection setup, latency and round trips — and worth
nothing at all for quota. A tool that counted batches would report a universe
download as costing 12 requests, then hit a wall at symbol 40 with no
explanation.

So the ledger tracks four quantities and never derives one from another:

* **requests** — HTTP calls actually issued.
* **credits charged** — what the plan was billed, from the vendor's own headers
  where it reports them, and from the provider's pricing model where it does
  not. Which of the two is recorded, so an estimate is never quoted as a
  reading.
* **credits remaining** — only ever from the vendor. Never inferred by
  subtracting from an assumed allowance, because the allowance is a guess and a
  wrong one produces confident nonsense.
* **waiting** — seconds spent deliberately paused. Reported so a slow run is
  explicable without anyone guessing whether it hung.

**The minute wall and the day wall are different problems.** Running out of
per-minute credits means waiting under a minute; running out of the daily
allowance means coming back tomorrow. Conflating them either wastes a day or
spins uselessly for one, so :class:`CreditLedger` distinguishes them and the
runner stops cleanly on the second.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from tradeit.acquisition.base import CreditUsage

#: How close to the reported floor the ledger will run before pausing. Not
#: zero: the vendor's counter and ours are separated by a round trip, and
#: sailing to exactly zero means discovering the wall by hitting it.
CREDIT_SAFETY_MARGIN = 2

#: Seconds to wait when the per-minute allowance is spent. Twelve Data's window
#: is a minute; waiting slightly longer than one avoids landing on the boundary.
MINUTE_WINDOW_SECONDS = 62.0


@dataclass(slots=True)
class CreditLedger:
    """Running account of what a run has spent and what it has left."""

    #: Credits per minute the plan allows. Used only to pace requests when the
    #: vendor reports no remaining figure; a vendor reading always wins.
    per_minute_allowance: int | None = None
    requests: int = 0
    credits_charged: int = 0
    credits_estimated: int = 0
    #: Last figure the vendor reported. ``None`` means it never told us, which
    #: is not the same as zero.
    credits_remaining: int | None = None
    credits_used_reported: int | None = None
    waited_s: float = 0.0
    pauses: int = 0
    #: Set when the vendor says the daily allowance is gone. The run stops.
    daily_exhausted: bool = False

    _window_started: float = field(default_factory=time.monotonic, init=False)
    _window_credits: int = field(default=0, init=False)

    # -- recording -----------------------------------------------------------

    def record(self, usage: CreditUsage, *, charged_fallback: int = 0) -> int:
        """Account for one response. Returns the credits attributed to it."""
        self.requests += 1
        charged = usage.charged if usage.charged is not None else charged_fallback
        self.credits_charged += charged
        self._window_credits += charged
        if usage.estimated or usage.charged is None:
            self.credits_estimated += charged
        if usage.remaining is not None:
            self.credits_remaining = usage.remaining
        if usage.used is not None:
            self.credits_used_reported = usage.used
        return charged

    def note_wait(self, seconds: float) -> None:
        self.waited_s += seconds
        self.pauses += 1

    # -- pacing --------------------------------------------------------------

    def should_pause(self, next_cost: int) -> float:
        """Seconds to wait before spending ``next_cost`` credits, or 0.

        Two independent reasons to pause, checked in order of authority:

        1. The vendor said how many credits are left, and this request would
           take us under the safety margin. Trust the vendor's number.
        2. We have no vendor figure, and our own count for this minute is at
           the plan's stated allowance. Fall back to pacing ourselves.
        """
        if self.credits_remaining is not None:
            if self.credits_remaining - next_cost < CREDIT_SAFETY_MARGIN:
                return MINUTE_WINDOW_SECONDS
            return 0.0

        if self.per_minute_allowance is None:
            return 0.0
        elapsed = time.monotonic() - self._window_started
        if elapsed >= MINUTE_WINDOW_SECONDS:
            self._window_started = time.monotonic()
            self._window_credits = 0
            return 0.0
        if self._window_credits + next_cost > self.per_minute_allowance:
            return max(MINUTE_WINDOW_SECONDS - elapsed, 0.0)
        return 0.0

    def open_new_window(self) -> None:
        """Called after a pause: the minute has turned over."""
        self._window_started = time.monotonic()
        self._window_credits = 0
        # The vendor's remaining figure is now stale in the optimistic
        # direction. Clearing it makes the next decision fall back to our own
        # pacing rather than to a number we know has expired.
        self.credits_remaining = None

    # -- reporting -----------------------------------------------------------

    def estimate_remaining_cost(self, symbols_left: int, per_symbol: int) -> int:
        """Credits still required, from measured per-symbol cost."""
        return max(symbols_left, 0) * max(per_symbol, 0)

    def measured_cost_per_symbol(self, symbols_done: int) -> float | None:
        """What a symbol actually cost this run, or ``None`` if nothing did.

        Measured rather than assumed, so the projection in the summary is a
        statement about this account and this plan rather than about the
        documentation.
        """
        if symbols_done <= 0:
            return None
        return self.credits_charged / symbols_done

    def to_payload(self) -> dict[str, object]:
        return {
            "http_requests": self.requests,
            "credits_charged": self.credits_charged,
            "credits_charged_estimated": self.credits_estimated,
            "credits_remaining_reported": self.credits_remaining,
            "credits_used_reported": self.credits_used_reported,
            "waited_seconds": round(self.waited_s, 1),
            "pauses": self.pauses,
            "daily_exhausted": self.daily_exhausted,
        }

    def render(self) -> list[str]:
        lines = [
            f"HTTP requests        : {self.requests:,}",
            f"API credits charged  : {self.credits_charged:,}"
            + (
                f"  ({self.credits_estimated:,} estimated from the pricing model, "
                "not reported by the vendor)"
                if self.credits_estimated
                else "  (reported by the vendor)"
            ),
        ]
        if self.credits_remaining is not None:
            lines.append(f"API credits remaining: {self.credits_remaining:,}")
        else:
            lines.append("API credits remaining: not reported by the vendor on the last response")
        if self.waited_s:
            lines.append(
                f"Paused for limits    : {self.waited_s:.0f}s across {self.pauses} pause(s)"
            )
        return lines


__all__ = ["CREDIT_SAFETY_MARGIN", "MINUTE_WINDOW_SECONDS", "CreditLedger"]
