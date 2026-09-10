# Pre-registration — `pattern_quality` by its real detector

Committed **before** the full-universe run finished, which is the only thing
that makes a pre-registration worth anything. The scratchpad copy this was
taken from is session-local and would have been lost; the git timestamp is
the evidence that the specification preceded the result.

```
PRE-REGISTRATION -- pattern_quality by its real detector
written 2026-09-10, BEFORE any run on this data

SIGNAL
  pattern_quality_best = max(quality) over every pattern the 12 enabled D1
  detectors report as of session T, computed on the trailing window only.
  Undefined (no observation) when no detector fires.

  Secondary, reported but a SEPARATE question:
  pattern_present = 1 if any detector fires at T, else 0.

DIRECTION -- declared, not derived
  pattern_quality_best : POSITIVE.
  Justification that precedes the data: ScoringConfig weights this factor
  positively, i.e. the system already asserts higher quality -> better
  candidate. Measuring against that assertion is the test. A DERIVED
  direction would cost 2 trials; this costs 1.

HORIZONS      21 and 63 sessions
QUANTILE      0.2 (top vs bottom quintile of quality)
STRIDE        21 sessions; overlap correction applied for the 63s horizon
UNIVERSE      2000-01-03 .. 2009-12-31, security_spans.csv, both arms
              (survived and died), same construction as every prior run
MIN OBS       500 after overlap correction
HURDLE        |t| > 2.0 floor, raised by the multiple-testing ledger

WHAT WOULD COUNT AS THE FACTOR SURVIVING
  An ESTABLISHED quantile spread in the declared direction, at either
  horizon, that is not OUTLIER_DEPENDENT and clears the ledger hurdle.

WHAT WOULD NOT
  A significant t on the IC alone with no established spread. That is what
  the SMA proxies produced, and it did not license a weight.

TRIALS ADDED  2 (one signal x two horizons). pattern_present is scored as a
              further 2, and reported separately so it cannot be swapped in
              for the quality result if quality fails.

--------------------------------------------------------------------
AMENDMENT 1 -- 2026-09-10, after a 6-security pilot, before any
filtered result was computed.

WHAT CHANGED
  pattern_quality_best now maxes over patterns in the ACTIONABLE states
  only -- MATURE, NEAR_BREAKOUT, BROKEN_OUT_UNCONFIRMED -- instead of over
  every instance the scan returned.

WHY, ON GROUNDS INDEPENDENT OF ANY OUTCOME
  The pilot showed a median of 9 and a maximum of 18 concurrent structures
  per scan, and the unfiltered max was drawing from INVALIDATED ones. The
  code's own semantics settle it without reference to returns:
    FORMING     "Recorded, not actionable."
    MATURE      "This is the state a screen would surface."
    INVALIDATED / EXPIRED  terminal, is_terminal == True
  A dead structure supplying a live candidate's quality is a construction
  defect, not a finding. Scoring a candidate on a pattern that has already
  failed is not what the factor means.

WHAT WAS NOT LOOKED AT
  No filtered result existed when this was written. The pilot's unfiltered
  numbers (IC -0.054 t -1.11 at 21s, IC -0.122 t -1.44 at 63s, n=420,
  6 securities) are recorded here so the amendment cannot be mistaken for
  a reaction to a disappointing number -- they were already null.

TRIALS  4 added in total: quality x 2 horizons, presence x 2 horizons.
        The pilot is not counted; it sized the run and fixed the defect.
```
