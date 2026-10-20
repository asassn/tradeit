# Runbook — what to do when something is wrong

Phase 12 asks for operational runbooks. This is the first one, and it is
deliberately short: a runbook nobody reads is as useless as a monitor nobody
runs, and most of what a long one would contain belongs in the code that
enforces it instead.

**Start here, always:**

```
PYTHONPATH=src .venv/bin/python -m tradeit.cli status --db sqlite:///research01.sqlite
```

It computes nothing, finishes in about two seconds, and names the database it
read. **Check that name first** — the configured DSN and the `research-01`
corpus are different databases, and the commonest confusion in this project is
reading one while meaning the other.

---

## "Trading has stopped and I do not know why"

Three things stop trading, they are enforced in different places, and the
distinction matters because they need different responses.

### 1. The portfolio is halted on a reconciliation discrepancy

**What it means:** our model of the book disagrees with the broker's. Every
decision taken afterwards would be computed from a book that does not exist.

**How to tell:** `BrokerExecutionService.is_halted(portfolio_id)` is True, and
`halt_reason(portfolio_id)` lists typed `Discrepancy` values — kind, instrument,
both views. A `DISCREPANCY` entry is in `journal_entries` with the same detail.

**What to do:** read the discrepancies. `magnitude` is `None` when one side has
no value at all, which means a position exists on one side only — a different
problem from a quantity that drifted. **The broker is authoritative.** Correct
our state to match, never the reverse.

**To resume:** `service.resume(portfolio_id, session_date=…, resolved_by=…,
note=…)`. Both the name and the note are required, and the halt **will not
clear itself** — a later reconcile that happens to agree leaves it standing,
because a disagreement that resolves itself is where silence is most dangerous.

### 2. The kill switch is thrown

**What it means:** order placement is stopped, either by the system detecting
its own problem or by an operator.

**How to tell:** `KillSwitch.engaged_reason` carries the reason. Orders raise
`OrderRefusedByControl`, and each refusal is journalled as `DECLINED`.

**What to do:** if it was thrown **by file**, a process cannot clear it —
deliberately, because that one is the operator's and clearing it would overrule
the person who threw it. Delete the file yourself once the cause is understood.
If it was thrown in-process, `disengage(cleared_by=…, note=…)` requires both.

**An unreadable switch file counts as thrown.** That is not a bug: a control
that stopped working when the filesystem was in trouble would be worthless
exactly when it was needed.

### 3. A pre-trade control refused the order

**What it means:** the order was wrong, or the data behind it was.

**How to tell:** `OrderRefusedByControl.refusals`, each with its kind.
`STALE_DATA` means the instrument's data is older than the configured
tolerance — or has never been seen, which is the most stale a thing can be.
`DUPLICATE_ORDER` means this trade was already sent, possibly **under a
different id**, which is the case a venue's idempotency key cannot catch.

**What to do:** stale data is an ingestion problem, not a trading one. A
duplicate is a decision problem: something decided twice, and the second
decision is the one to investigate.

---

## "Is this strategy ready?"

```
.venv/bin/python scripts/readiness_report.py
```

It reports `PROMOTE` or `HOLD` per rung with every criterion's verdict. Read the
verdicts, not the headline:

* **`FAIL`** — measured, and not good enough. Needs better evidence.
* **`UNRESOLVED` / "not measured"** — nobody ran the test. Needs a run.
* **`UNRESOLVED` / "threshold not approved"** — needs a **decision**, and no
  amount of further work will supply it. Four criteria are in this state by
  design.

**`HOLD` is the current correct answer** for everything this project has built.
There is no validated out-of-sample edge after 144 ledger trials.

---

## "Has a running strategy decayed?"

`readiness.decay.assess_decay(expectation, live_returns)`.

**Read `minimum_detectable_shortfall` before the verdict.** Below thirty live
trades there is no verdict at all, and above it a `CONSISTENT` verdict whose
detectable floor exceeds the whole expected edge is *compatible with the edge
having disappeared* — the assessment says so in words. A green light with a
large floor is not evidence the strategy is working.

**`OUTPERFORMING` deserves the same attention as underperformance.** Beating a
backtest by more than sampling noise usually means the backtest was wrong.

---

## "Why did it do that?"

The decision record answers it, and the read side serves it:

```bash
.venv/bin/pip install -e ".[api]"
.venv/bin/uvicorn --factory "tradeit.api:create_app" --host 127.0.0.1 --port 8000
```

`create_app` takes the record's DSN and has **no default**, so a wrapper is
needed to pass one — deliberately, because the record and the corpus are
different databases and a service that defaulted to the configured DSN would
read whichever happened to be configured.

Fill the record first, or every answer is empty:

```bash
.venv/bin/python scripts/record_decisions.py --tickers AAPL,MSFT,IBM
```

**Read `missing` before anything else.** `GET /sessions/{date}` returns the
links that hold no rows, and that field is the difference between *nothing
happened* and *nobody wrote it down*. A session with empty lists and no
`missing` was quiet; one with empty lists **and** `missing` was not recorded.

A decision session showing orders and **no executions is correct** — the engine
settles yesterday's decisions against today's prices, so the execution belongs
to the next session.

**The service has no write routes and no trading authority.** A test asserts
that, and the request session is rolled back on the way out so a route that
accidentally wrote could not persist it.

**There is no buy/sell verdict and will not be one.** `GET
/instruments/{id}/scores` returns the scores as recorded and computes nothing
across them: a single number across horizons is what the three-horizon
architecture exists to prevent.

---

## "Can this place a real trade?"

**No, and not by accident.** `trading_mode=live` requires a machine-local
authorisation file that does not exist; `Settings` refuses to construct without
it, so live mode cannot even be configured. `PaperBroker` additionally refuses
to be constructed where orders are not simulated, checking for itself rather
than trusting its caller.

`tradeit status` reports the interlock state. **Completing every roadmap phase
does not authorise live trading** — that is the owner's decision and a separate
act.

---

## What this runbook does not cover

**Incident response for a live system, because there is no live system.**
Writing a procedure for an incident class that cannot yet occur would produce a
document that is wrong by the time it is needed. It is owed when the first
strategy reaches `LIMITED_LIVE`, and not before.
