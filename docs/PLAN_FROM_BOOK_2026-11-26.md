# `tradeit plan` reads the recorded book — and what running it daily exposed

**2026-11-26.** Not a trial; the ledger does not move (145).

## What changed

`tradeit plan` read every holding from a JSON file the operator edited, because
until 2026-11-24 nothing wrote a `positions` row. `scripts/paper_live_shape.py
--record` now writes the book at every close, so a horizon can name that book
instead:

```
tradeit plan --tickers ... --start 2012-01-03 --as-of 2014-06-09 \
    --book book.sqlite --portfolio e2e
```

or `"book": {"record": "book.sqlite", "portfolio": "e2e"}` in a declaration,
relative to the declaration file. Cash, positions, stops and the three ladder
fields come from the record; the universe, warmup, score range and signal
windows stay declared, because the record holds what a portfolio owns, not how
it decides. Code: `src/tradeit/decide/book.py`, `runner.py`.

**What a declaration could not give and a book does:** the real high since
entry, sessions held and partial-profit flag. The declared path approximated the
high from one bar and said so. The book path approximates nothing.

**The plan advances the book exactly one session, as the driver does** —
`LiveShapedSession.run_session` marks, increments `sessions_held` and raises the
high to the close, then decides. So the plan must be for the session
immediately after the last recorded close. Refused rather than reported, each
because it would plan a book that does not exist:

| refused | why |
|---|---|
| a session already recorded | it has been decided |
| a gap of skipped sessions | no stop was checked in them; the message names the right session |
| a warmup starting after the book's last close | the gap would fall outside the window, unseen |
| a held instrument outside the universe | it cannot be marked or stopped |
| a split on the planned session | pre-split units against post-split bars read as a collapse |
| a position with no ladder, or no initial stop | a trailing stop from a reset high is looser than the one in force |
| a book never recorded at a close | "never recorded" is not "holds nothing" |
| hand-stated equity, cash or holdings beside a book | two sources for one book is a question about which is current |

The record is opened read-only; a missing file is refused before connecting,
because SQLite creates one on connect. Thirty tests; ten mutants, all caught.

**One thing the record could not see:** orders decided at the last recorded
close and not yet filled. That turned out to matter more than a caveat — see
below, and the fix after it.

## The measurement

A real book: 8 tickers (6 resolve), 2012-01-03 to 2014-06-06, recorded by the
paper driver — 610 sessions, 3 positions open, the same cut the 2026-11-24
resume measurement used. Then, for each of the **144 sessions** to 2014-12-31:
plan the session from the book, then let the paper driver run exactly that
session with `--resume`, and compare.

| | result |
|---|---|
| sessions compared | 144 |
| ladder state (sessions held, high) the plan used vs what the driver wrote | **432 of 432 agree** |
| days the plan's exits differ from the driver's orders | **0** — 379 and 379 |
| entries | plan 2, driver 0 — expected: the driver ranks with `DiversityAwareRanker` and five rules, the plan with the fit ranker, the archive's rules and the correlation rule |

**The plan and the driver agree.** What they agree on is the finding.

## The defect: a paper book run one day at a time never trades

From 2014-07-07 both sides propose the same three exits **every session for
six months**, and none is ever filled:

| | one process, 754 sessions | one process per session |
|---|---|---|
| orders placed / filled | 20 / 20 | **379 / 0** |
| at 2014-12-31 | flat, equity 100,944.67 | 3 positions held **164–168 sessions** under a **40-session** time stop |

An order decided at the close fills at the next session's bars. The record
holds positions, not the broker's open orders, and `PaperBroker.restore` takes
cash and holdings only — so **every order in flight when a process ends is
dropped**, the next process decides the same exit again, and drops it again.
Entries are lost the same way.

The 2026-11-24 resume measurement passed because its single cut, 2014-06-06,
happened to have no order in flight: one cut tested one boundary, and this
boundary was not it. Its reconciliation result stands; its claim that a
stopped run "is one run" holds **only for a cut with nothing in flight**.

This is the failure that matters for operation, because one process per day is
what operation *is*. Under it, every exit control — stop loss, time stop,
partial profit — decides correctly and executes never.

## Fixed the same day, and measured

The paper session now writes every order to `orders` at each close
(`sync_orders`), with the protective stop an entry was decided with in a new
column, `orders.intended_stop_price` (migration 0023; not `stop_price`, which
is a STOP order's trigger at the venue). `--resume` reads the orders still
resting (`load_open_orders`), re-derives each one's reference bar from the
corpus — it sets the fill price — and restores them into the venue with their
original broker ids, so `advance` fills them in the same order.

The same 144 sessions, one process per session, against one process:

| | one process, 754 sessions | 610, then 144 processes of one session |
|---|---|---|
| orders / fills | 20 / 20 | **20 / 20** — identical rows, intended stops included |
| `positions` rows | 10 | **10**, every field agreeing |
| snapshots | 754 | **754**, cash and equity within 4.8e-7 |
| at 2014-12-31 | flat, 100,944.67 | **flat, 100,944.67** |

The 4.8e-7 is `Numeric(18,6)` truncation, compounded over 144 restarts where
the 2026-11-24 measurement had one (4.8e-8 there). All 144 sessions reconciled
clean; eleven of them had an order in flight across the restart.

Refused rather than restored: an entry with no recorded stop, a resting order
already part-filled (the filled part is in the position, so restoring the
whole would buy it twice), an order from another account, and an order whose
decision bar is missing from the corpus. Eight tests; ten mutants, all caught.

**`tradeit plan` now refuses over an order in flight**, naming it, instead of
carrying a blanket caveat. The order fills at the planned session's bars before
that session decides, and the plan does not simulate a fill, so it would
propose, say, selling a position the venue had sold that morning. The paper
session is the thing to run on such a day. A record written before orders were
recorded still carries the caveat, because it cannot tell "nothing in flight"
from "nothing recorded".

## Reproduce

```
.venv/bin/python scripts/paper_live_shape.py --tickers AAPL,MSFT,JNJ,XOM,PG,KO,WMT,JPM \
    --start 2012-01-03 --end 2014-06-06 --record book.sqlite --name e2e
```

then, per session, `tradeit plan ... --book book.sqlite --portfolio e2e --as-of D`
followed by the same paper command with `--end D --resume`.
