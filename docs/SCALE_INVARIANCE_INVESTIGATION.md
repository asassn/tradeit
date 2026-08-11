# Scale invariance: 32 violations, two defects, one cause

The first real snapshot (`twelve_data-daily-e3ddc03209bb25b4`, 78 instruments,
293,811 bars) failed `phase3.scale_invariance` with 32 violations spanning
`adx_14`, `plus_di`, `minus_di` and `volatility_percentile`.

This document records what was measured, what caused it, and why the fix is a
fix rather than a widened tolerance.

---

## 1. The property, checked before the code

A split adjustment multiplies every price in a window by a positive constant.
So a computation that reads only prices and is unchanged under `p → λp` gives
the same answer on adjusted prices as on raw ones. That is the licence to run
the scale-invariant analytics on the 44 instruments with no verified raw
series.

For ADX the property is provable, not assumed:

| quantity | under `p → λp` |
|---|---|
| `TR = max(hᵢ−lᵢ, \|hᵢ−cᵢ₋₁\|, \|lᵢ−cᵢ₋₁\|)` | `→ λ·TR` — a max of absolute differences of prices |
| `+DM`, `−DM` | `→ λ·DM` — differences of prices, and the comparison `up > down` is order-preserving under a positive factor |
| Wilder smoothing | linear, so `→ λ·` the same |
| `±DI = 100·smooth(±DM)/smooth(TR)` | **λ cancels** |
| `DX = 100·\|+DI−−DI\|/(+DI+−DI)` | already λ-free |
| `ADX` | a smoothing of `DX` |

So ADX *is* invariant in exact arithmetic. The violations were therefore
either a defect in the implementation or a defect in the harness — not a wrong
declaration.

`realized_volatility` and `volatility_percentile` are invariant by the same
argument: log returns are `log(pᵢ/pᵢ₋₁)`, in which λ cancels inside the ratio.

---

## 2. The measurement

Three price regimes × three seeds × six factors × 1,008 sessions, against the
real `IndicatorEngine`. Complete census of violating indices, **before** the
fix:

| feature | 2.0 | 0.5 | 8.0 | 0.125 | 3.7 | 1000.0 |
|---|---:|---:|---:|---:|---:|---:|
| `adx_14` | 0 | 0 | 0 | 0 | **4,341** | **4,281** |
| `plus_di` | 0 | 0 | 0 | 0 | **3,649** | **3,116** |
| `minus_di` | 0 | 0 | 0 | 0 | **3,532** | **3,370** |
| `volatility_percentile` | **9** | **8** | **6** | **4** | **5** | **5** |

**22,326 violating indices in total.** Worst relative difference for `adx_14`:
4.87e-01 — half the value of the indicator, not a rounding wobble.

Two things in that table are the whole diagnosis.

**ADX fails only at 3.7 and 1000.0.** `2.0`, `0.5`, `8.0` and `0.125` are exact
powers of two, so multiplying by them is exact in binary floating point — the
mantissa is untouched and only the exponent moves. A feature that survives
exact rescaling and breaks under inexact rescaling is not accumulating
arithmetic error; it is taking a **different branch**.

**`volatility_percentile` fails at every factor, including the exact ones.**
Different mechanism, same class.

Sample rows (regime `penny`, seed 7, factor 3.7):

```
feature       factor  index      baseline        scaled     abs diff   rel diff
adx_14           3.7     63   23.80095609   24.52442601   7.2347e-01 3.0397e-02
adx_14           3.7     65   21.45793433   23.24562474   1.7877e+00 8.3311e-02
adx_14           3.7     74   17.50949762   19.27917526   1.7697e+00 1.0107e-01
```

---

## 3. Defect 1 — a Wilder tie decided by the last bit

Traced to a single bar. High goes `3.01 → 3.06`; low goes `3.01 → 2.96`. The
up-move and the down-move are **both exactly 0.05**, and Wilder's rule for that
case is explicit: neither direction wins, so `+DM = −DM = 0`.

In IEEE-754 the two differences are not equal:

```
3.06 - 3.01 = 0.050000000000000266
3.01 - 2.96 = 0.049999999999999822
```

so `up_move > down_move` is `True` and the pre-fix kernel recorded `+DM = 0.05`.
Multiply every price by 3.7 and the same two subtractions produce
`0.18499999999999872` and `0.18500000000000050` — the comparison reverses, and
the kernel records `−DM` instead. Wilder's smoothing is recursive, so one
flipped bar propagates through the rest of the series.

**This is a correctness bug independent of splits.** The pre-fix indicator gave
a different ADX for the same bars quoted in dollars and in cents. Scale
invariance is how it surfaced, not what it was.

Tie counts by regime, per 1,007 transitions:

| regime | exact H/L ties |
|---|---:|
| large cap, $180, smooth | 0 – 2 |
| penny, $3, tick-quantized | 40 – 67 |
| illiquid, $12, repeated H/L | 161 – 168 |

Which is exactly why the original fixture passed: a smooth large-cap series
produces almost no ties, and the real universe produces them constantly.

### The fix

```python
TIE_EPSILON_FACTOR = 32.0

def tie_tolerance(reference: Floats) -> Floats:
    return TIE_EPSILON_FACTOR * _FLOAT_EPS * np.abs(_as_float(reference))
```

The tolerance is **proportional to the magnitude of the inputs**, because that
magnitude is what bounds the cancellation error in `a − b`. A fixed absolute
epsilon would be unit-dependent — the exact defect being fixed. `+DM`/`−DM` are
then decided only when the two moves differ by more than that.

### Why this is not "widening the tolerance until it passes"

- **The tolerance is 5 · 10¹¹ times smaller than the smallest real move.** On a
  $3 stock the tie tolerance is ≈ 7e-15 relative; one tick is 3.3e-3 relative.
  Eleven orders of magnitude of headroom.
- **It is a no-op where the moves are distinguishable.** A permanent test
  compares the shipped kernel against the naive comparison it replaced and
  asserts they agree *bit for bit* at every decisive index, and that only the
  indistinguishable ones changed — to Wilder's stated answer of zero.
- **The gate's own tolerance was not touched.** `SCALE_TOLERANCE` is still
  `1e-9` and `SCALE_ATOL` still `1e-12`.
- **A mutation test proves the harness still bites.** Planting a scale-
  sensitive quantity under an invariant-sounding name is still caught.

### Independent cross-check

The project's Phase 3 indicators are its own NumPy kernels — there is no
third-party indicator library in the dependency set to compare against. As an
outside check, the same experiment was run against the `ta` package's
`ADXIndicator` (installed in the dev environment; **not** a project dependency
and not added as one):

```
ta  factor=2.0      adx  disagreeing indices=    0   max rel=0.00e+00
ta  factor=3.7      adx  disagreeing indices=  976   max rel=5.38e-01
ta  factor=1000.0   adx  disagreeing indices=  981   max rel=4.25e-01
```

The identical signature — clean at the dyadic factor, hundreds of disagreements
at the non-dyadic ones, same order of magnitude — from an unrelated
implementation confirms two things: the invariance property is real (both
implementations agree exactly when the rescaling is exact), and the tie defect
is a general property of the naive comparison rather than something peculiar to
this codebase.

`ta` was used **only** for that property. Its ADX levels are not comparable to
this project's: it seeds the smoothing with a windowed sum and indexes the
result differently, so the absolute values differ substantially and it is not a
reference for correctness of the indicator's value.

---

## 4. Defect 2 — a rank step taken on a one-ulp difference

`volatility_percentile` failed at every factor, through a different path.

`realized_volatility` computed log returns as `np.diff(np.log(close))`. The
difference of two logarithms of nearly equal numbers is catastrophic
cancellation: the leading digits agree and cancel, so the *last bits of the
result* depend on the absolute price level. Mathematically
`log(a) − log(b) = log(a/b)`; in floating point they are not the same number.

`percent_rank` then counted `windows <= subjects` — a discrete count. One ulp
of difference moves a value across the comparison and the rank changes by a
whole step of `1/(count−1)`, which for a 20-day window is 5.3e-2. Hence the
observed 7.69e-02.

Two fixes, one per half:

```python
# realized_volatility: form the ratio first, then take one logarithm
positive = np.where(close > 0, close, np.nan)
log_returns = np.log(positive[1:] / positive[:-1])

# percent_rank: count values that are at or below within representation error
tolerance = tie_tolerance(np.maximum(np.abs(windows), np.abs(subjects)))
at_or_below = (finite & (windows <= subjects + tolerance)).sum(axis=1)
```

The first is strictly more accurate arithmetic and would be right regardless of
this investigation. The second applies the same magnitude-proportional
tolerance as the ADX fix, for the same reason.

---

## 5. Result

Re-running the identical sweep with the fixes in place:

```
TOTAL VIOLATIONS: 0   over 3 regimes x 3 seeds x 6 factors x 1008 sessions
```

`tests/unit/test_scale_invariance.py` — 71 tests, covering exact-tie semantics,
dollars-versus-cents equality, penny-priced invariance at nine factors, the
tolerance's distance from one tick, the no-op property above, and a mutation
test showing a planted scale-sensitive feature is still caught.

## 6. What this does not claim

Split adjustment is **piecewise** constant. A window straddling a split is
rescaled non-uniformly, and an invariant feature computed across one still
reads a series nobody could have seen. That limitation is recorded separately in
`docs/VALIDATION_ELIGIBILITY.md` and nothing here softens it.
