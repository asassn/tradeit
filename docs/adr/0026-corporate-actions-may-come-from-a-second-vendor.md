# ADR-0026: Corporate actions may come from a second vendor, and the package says so

**Status:** accepted
**Date:** 2026-08-10
**Supersedes:** nothing. **Amends:** ADR-0005 (raw prices are what this platform
stores) by describing how a raw series is obtained when the price vendor does
not sell one.

## Context

Twelve Data's daily bars are split-adjusted. ADR-0005 says this platform stores
raw exchange prints, because today's adjusted series for a stock that split last
week differs from the series anybody could have seen before the split, and every
pattern drawn on the adjusted history is drawn on a history that never existed.

Recovering the raw prints from adjusted ones is exact arithmetic — multiply each
price by the product of every split ratio effective after it — and requires one
input the price vendor was supposed to supply: the split schedule. On the plan
this project holds, Twelve Data's `/splits` endpoint answers with a plan
restriction.

The acquisition code already refuses to conflate "not on your plan" with "this
security never split", so the honest outcome was a package whose reconstruction
is marked *not attempted*. That is correct and useless. The split schedule is
obtainable — FMP serves one on a free account — but from a **different vendor**.

That raises the question this ADR answers: what is a price that was computed
from one vendor's adjusted bars and another vendor's split schedule, and how
does a package say so?

## Decision

**1. A corporate-action source is a different kind of thing from a price
provider, and the type system says so.**

`AcquisitionProvider` plans a download, owns batching and credit pricing, and
writes a package. `CorporateActionSource` answers one question per symbol and
writes nothing. They have separate registries, separate command-line options,
and no shared base class. `FmpSplitSource` has no `plan`, no `fetch`, no
`adjustment_policy` and therefore no way to produce a bar.

This is structural rather than a convention because the failure it prevents is
undetectable afterwards: a package whose prices quietly came from a vendor other
than the one its manifest names looks exactly like one that did not.

**2. Enrichment is a pass over an existing package, not a step inside
acquisition.**

`tradeit data enrich <package> --source fmp` is the primitive. It reads a
package that already has prices, fetches split schedules, writes `splits.csv`,
re-derives the reconstruction, and rewrites the manifest. It downloads no bars.
`tradeit data acquire --split-provider fmp` runs exactly that pass immediately
after acquiring — sugar, not a second implementation.

The reason is the free plan's shape. Price acquisition spans days: the daily
credit allowance runs out, the run stops cleanly at
`ACQUISITION_INCOMPLETE_QUOTA`, the operator resumes tomorrow. A package
therefore sits usable-but-incomplete for a long time, and the split schedule for
the symbols already downloaded is useful immediately. Requiring a re-acquisition
to get it would either waste the allowance or discourage getting it at all.

**3. The manifest gains a `[provenance]` table, and the reconstructed value is
attributed to neither vendor.**

`provider` at the top level continues to name the *price* source, so a package's
identity does not change when it is enriched. Alongside it:

```toml
[provenance]
price_provider = "twelve_data"
price_representation = "split_adjusted"
split_provider = "fmp"
reconstruction_performed = true
reconstruction_algorithm = "split-inverse-1"
reconstruction_label = "RECONSTRUCTED_RAW_FROM_SPLIT_ADJUSTED"
```

Every reconstructed row carries both provider names as separate columns. The
label never becomes `RAW_VENDOR_PRICE` and the values never enter the package's
price columns.

**4. The vendor's numerator and denominator are kept, not just the ratio.**

A ratio of `0.1` could be 1-for-10 or 2-for-20. When somebody later disputes the
direction of a reverse split, the vendor's own pair is what the argument gets
settled against, and a derived decimal is not.

**4a. (Amended after the first live smoke test.) Split factors are normalized
through a per-provider declared convention before anything compares them.**

The first real run reported Apple's 2014 and 2020 splits as cross-provider
conflicts: Twelve Data served `0.142857…` and `0.25` where FMP served 7-for-1
and 4-for-1. Those are reciprocals of each other — one corporate action seen
from opposite ends — and the previous code compared raw vendor numbers.

`SplitEvent` now carries one canonical quantity, the share-count multiplier,
with the price-adjustment, volume-adjustment and reconstruction multipliers as
derived properties. Each provider **declares** how its factors are read;
nothing is inferred from a value, because `0.25` is the price factor of a
4-for-1 and the share factor of a 1-for-4 and no inspection separates them. A
provider whose convention is not established raises rather than guessing. The
vendor's own value and its convention are preserved in the package.

This also corrected a live defect: the Twelve Data adapter's factor direction
was reciprocal to the truth, which would have multiplied pre-2014 Apple prices
by 1/28 instead of 28 had that plan included the endpoint. Full write-up in
`docs/VENDOR_SEMANTICS.md`.

**5. An effective date is not an announcement date, and the gap is left open.**

FMP's `/stable/splits` carries the ex-/effective date. When the split became
publicly *knowable* is a different fact the endpoint does not hold, so
`announced_at` stays `None` and
`corporate_action_knowledge_timestamps` is reported `NOT_AVAILABLE_ON_PLAN`.
Reconstructing prices does not solve announcement-time corporate-action
causality, and the manifest says so rather than letting the improvement imply a
larger one.

**6. Disagreements are reported, never reconciled.**

Where a package already held splits and the enrichment source differs, both
facts go in the report and the manifest, the named source's schedule is used
because the operator named it, and every difference is listed. Averaging two
ratios or silently dropping a duplicate would produce a reconstruction that
looks clean and is wrong in a way nothing downstream could detect.

A split that merely falls outside the package's date window is *not* a conflict.
It is correct behaviour — a 2005 split affects nothing in a package that starts
in 2010 — and is reported as a note, so the conflicts that do need a person are
not buried.

Neither is a reciprocal pair, after 4a. If two *normalized* ratios come out
reciprocal, a provider's declared convention is wrong; that is a different
problem from the vendors disagreeing and has a different fix, so it is reported
as its own kind of finding rather than as an economic conflict.

**7. (Added after the first live smoke test.) A split count is four numbers.**

The report said "reconstructed across 5 split(s)" for an Apple package spanning
2010 to 2025. True, and misleading: three of those five are from 1987, 2000 and
2005 and changed no row in it. `SplitCensus` reports records supplied, records
inside price coverage, records that changed at least one row, and records
outside coverage split by side — because a split *after* the window affects
every row while one *before* it affects none, and merging those loses the
distinction that matters. The reconstruction arithmetic was not changed for
this; only what the report says about it.

## Consequences

**Good.** A free-tier package can now carry a defensible raw price series
instead of an empty reconstruction. The seam that made this possible is one
protocol with one method; a dividend source, or a second split source, is a new
file rather than a refactor.

**Costly.** A package can now be internally heterogeneous, and anyone reasoning
about it has to read `[provenance]` rather than `provider`. That is a real
increase in what a reader must know, accepted because the alternative is a
single field that is quietly false.

**Unresolved, and stated rather than hidden.** The reconstruction's correctness
depends on FMP's split history being complete. A split FMP does not hold leaves
every price before it wrong by that split's factor, and **nothing in the data
reveals it**. This is better evidence than no split schedule at all. It is not
ground truth, and the manifest does not claim to be.

Equally unresolved: Twelve Data documents that daily bars are adjusted for
splits *only*. If they were also dividend-adjusted, this reconstruction would be
confidently wrong and no check here would notice. The arithmetic inverts what the
vendor says it did, which is a documentation claim rather than something this
project has verified.
