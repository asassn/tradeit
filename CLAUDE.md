# TradeIt — orientation

An AI-assisted stock research, screening, strategy, backtesting and portfolio
platform, built to behave like a portfolio manager rather than a stock picker.
The distinction it is organised around: *"a great stock"* and *"a great trade for
this portfolio right now"* are different questions, and only the second can
authorise a trade.

**This file orients a new session. It deliberately contains very few numbers**,
because numbers here would rot the way three documented counts already did
(`tests/unit/test_documented_counts.py` exists to stop that recurring). Measure
the state instead of reading it from prose.

---

## Decide. Do not ask.

**The owner is the product owner, not the engineer.** He decides what the system
is for and what it may spend; he should not be deciding column types, table
counts or migration strategy. A question written for an engineer costs him time
he cannot spend and produces a worse answer than deciding yourself would — he
has said so directly. **Default hard to deciding.**

### What the project is for — this settles most questions on its own

1. **Profitability is the first consideration.** The owner has said it plainly.
   Everything below serves it; none of it is an end in itself.
2. **A great stock and a great trade for this portfolio right now are different
   questions**, and only the second may authorise a trade. Anything that quietly
   collapses them is wrong however well it performs.
3. **A number that cannot be traced to evidence is worse than no number**, because
   it will be acted on. `UNRESOLVED` is a real answer. Fail closed.
4. **The corpus must not lie about survivorship.** That is what the current phase
   exists for. A backtest run on the survivors is not optimistic, it is fiction.
5. **Free work comes before paid work** — not because money is forbidden, but
   because most of what is needed is free and nobody has finished it yet.

### The destination — and what TradeIt is *not*

**It is not an EDGAR project, a vendor-validation project, a screener or a
historical-data project.** Those are supporting systems, and mistaking one for
the product is the standing failure mode of a codebase whose foundation work is
this large. The system is meant to run on a roughly **10–15 year horizon**,
adapting its research as markets change while validation and risk controls stay
strict.

**Three horizons, never collapsed.** Day, Swing and Retirement are separate
portfolios with separate decision systems, and *a ticker may reach different
conclusions at different horizons*. A universal Buy/Sell number would destroy
exactly the information the architecture exists to preserve.

Detail lives in [`docs/ROADMAP.md`](docs/ROADMAP.md) and its companions and is
not restated here, where it would rot:

| | |
|---|---|
| a trustworthy data foundation | prices, actions, fundamentals, identity, universes, delistings, news, macro. **Correctness precedes model sophistication** |
| per-ticker intelligence | an outlook per horizon, with supporting *and contradicting* signals |
| Strategy Builder | strategies declared, versioned, tested, compared, paper-traded, promoted — not hard-coded |
| signal research | which signals actually predict, by horizon, regime and sector, alone and combined |
| rigorous backtesting | point-in-time, survivorship, look-ahead, costs, slippage, liquidity, sizing. **A pretty equity curve is not evidence** |
| a governed research loop | hypothesis → backtest → out-of-sample → walk-forward → robustness → paper → limited live. **Not unrestricted self-modification** |
| a readiness framework | no single metric decides. `RESEARCH → ROBUST_BACKTEST → PAPER → LIMITED_LIVE → SCALED_LIVE`, and **demotion must be possible** |
| portfolios distinct from strategies | a strategy decides; a portfolio allocates and constrains. **Risk controls live outside a strategy's reach** |
| dashboard and onboard assistant | the assistant explains *actual system evidence and provenance*, never invented rationale |

**The question every architectural decision is judged against:**

> Does this make TradeIt more trustworthy at identifying and executing genuinely
> profitable opportunities **out-of-sample**, with controlled risk and
> reproducible evidence?

If it does not, it may not belong on the critical path.

### Decide it yourself when

The change is reversible in a commit, costs nothing, sends nothing outside this
machine, and does not touch the frozen list in *Constraints on the work itself*.
That covers nearly everything: schema shape, naming, test design, module
boundaries, which milestone to take next, how to resolve a conflict.

When you decide: **record the alternative you rejected and why**, in the code or
the document rather than only in the commit message. A decision whose reasoning
is not written down is one somebody will silently undo.

**Investigate before asking.** The answer is usually already in code, tests, git
history, the documents, the roadmap, primary evidence, or these rules. Ask only
when a genuine product, business or user decision is left after looking.

Decide, do not ask: implementation details, algorithms and data structures,
refactors, bug fixes, new and generalised tests, documentation, CLI usability,
internal APIs, module layout, non-destructive schema evolution an approved
capability requires, migrations, validation, logging, error handling, correcting
stale status text against measured facts, and technical debt on the critical
path. If a test is stale and the product behaviour is right, **update the test
and say why** — stating the invariant it was really asserting, per *Rework
decaying tests* below.

### Ask only when

- **It spends money.** Every purchase, every time.
- **Something leaves this machine** — an email, a vendor request, anything
  published.
- **It would reverse a written decision** — an ADR, the roadmap numbering,
  denominator methodology, detector thresholds, strategy parameters, backtesting
  assumptions, trading logic.
- **It changes what the corpus claims to be true** rather than how it is stored.
- **The evidence is genuinely ambiguous and proceeding would require inventing a
  fact.** Never guess an identifier to keep moving.

Two questions that feel like they qualify and do not: *"which of these two
designs is better?"* — that is yours; pick one and say why. And *"is this worth
doing?"* — if it is on the roadmap and costs nothing, it is.

Three more, which are the same rule in areas the list above does not reach:

- **Real-money trading.** Placing a live trade, enabling brokerage execution,
  increasing live capital, changing live risk limits, or moving a strategy from
  paper to live. The interlock in `src/tradeit/config.py` stands regardless.
- **Credentials and secrets.**
- **Destructive or irreversible operations** — deleting significant data,
  discarding evidence, rewriting history, force-pushing, lossy migrations,
  removing a capability. The Git policy already forbids several outright and
  this does not soften it.

**Stop conditions.** Stop and ask rather than guess when evidence cannot
establish a fact; primary identifiers conflict; a result would need fabricated
provenance; `origin` has diverged unexpectedly; work would cross into live
money; a purchase becomes necessary; or two product choices diverge materially.
**A stop caused by insufficient evidence is correct behaviour** — the
`UNRESOLVED`-is-first-class discipline applied to the work itself.

### How to ask, when you must

The owner reads these on a phone between other work. Optimise for that.

- **Lead with the decision in one plain sentence.** No schema terms, no file
  paths, no jargon above the fold.
- **Say what it costs to get it wrong** — in money, in time, or in what the
  system would wrongly believe.
- **Always recommend one option and say why.** A menu without a recommendation
  pushes the engineering decision back onto him, which is the thing to avoid.
- **One question per message.** Two questions get one answer and the wrong half
  gets guessed.
- **Technical detail goes below, clearly marked as optional.**
- **If he says "you decide", or does not answer, take your recommendation and
  proceed.** Standing authorisation.

### On proposing a purchase

Do not buy anything without approval — but **do not be shy about proposing one.**
The owner has said he will consider paying for what genuinely makes the system
better. A vague *"we could maybe buy X"* wastes that offer. A proposal that names
what it buys, what it costs, what it would let us measure that we cannot measure
now, and what specifically stays broken without it, is what he asked for.

---


### Keep going without being asked

**Do not stop after every commit to ask "what next?"** After a task: validate,
commit, push, confirm `HEAD` matches `origin` on a clean tree, update measured
state, pick the dependency-correct next task, and continue if it falls inside
these rules. Give concise checkpoints; do not make the owner relay instructions
between steps.

This supersedes *"do not start the next task automatically"* in the Git policy
for work already inside an agreed scope. That rule still binds when the next
step would cross a boundary above, or enter scope nobody has agreed.

**Roadmap autonomy.** Refine the *implementation* sequence when dependencies
demand it, insert bounded technical enablers, and fix debt on the critical path.
**Do not redefine the business roadmap** — its phase numbering is fixed by
written authorisation. New architecture may be built to reach an
already-approved goal when the need is demonstrated from repository behaviour,
the solution is principled, compatibility is kept where practical, tests protect
the invariant, and product intent is unchanged.

**Autonomy does not move the quality bar.** Fail-closed evidence handling,
provenance, point-in-time correctness, anti-look-ahead and anti-survivorship
safeguards, realistic backtesting, synthetic invariant tests, the full
validation baseline and Git discipline all continue to apply — and the
scoped-proposition rule for denominator methodology, detector thresholds,
strategy parameters, backtesting assumptions and trading logic is **not**
relaxed by any of this.


## Check the state before doing anything

```
git -C <repo> status --porcelain && git -C <repo> rev-parse HEAD
.venv/bin/python -m pytest -q                       # full suite
PYTHONPATH=src .venv/bin/python -m tradeit.cli edgar controls   # Milestone 0b state
```

`tradeit edgar controls` prints three separate measurements — identity, mapping
quality, adjudication completeness. **They are different questions**; do not
restate any of them from memory or from a document.

## Read in this order

1. [`docs/ROADMAP.md`](docs/ROADMAP.md) — canonical phase numbering, **fixed by
   written authorisation and not to be renumbered**. Three non-numbered gates sit
   between the numbered phases.
2. [`docs/PHASE_06_IMPROVEMENT_PLAN.md`](docs/PHASE_06_IMPROVEMENT_PLAN.md) —
   where work currently is, its milestone table and its measured status.
3. [`docs/RESEARCH_01_DATA_DICTIONARY.md`](docs/RESEARCH_01_DATA_DICTIONARY.md)
   — **read before querying the corpus.** Its §0 lists the ways the file
   returns a confident wrong answer, each discovered by getting one. Written
   for a reader with no other context, so it also serves anyone pointing
   another tool at the database. **Add to it whenever a new reading rule is
   found.**
4. [`docs/EDGAR_DELISTING_DENOMINATOR.md`](docs/EDGAR_DELISTING_DENOMINATOR.md)
   §4 — the identity and evidence rules. Normative.
5. [`docs/adr/`](docs/adr/) — 26 decisions that constrain later phases.

## Validation baseline for any production change

**This list mirrors `.github/workflows/ci.yml`. Anything CI runs that is not
here can drift locally until CI catches it, which is how two files sat
unformatted through several commits** — `ruff format` was never in this list, so
nobody ran it, and the job that would have objected was failing earlier for an
unrelated reason.

```
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests migrations
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy src
```

**`ruff check` and `ruff format --check` are different questions** and neither
implies the other: the first is lint, the second is layout, and a file can pass
one while failing the other. `migrations` is linted but deliberately not
format-checked, because CI does not format-check it either — mirror CI rather
than improving on it here, or this list drifts in the opposite direction.

CI runs `mypy src/tradeit`; `mypy src` is equivalent because `[tool.mypy]` sets
`packages = ["tradeit"]`, and both report the same file count — which is *not*
written here, because it rotted once already: this file said 164 long after the
answer became 218. CI adds coverage flags to `pytest`, which change no result.

Plus, where relevant: the CLI diagnostic, the schema loader, and citation
integrity. Inspect the final diff before committing.

---

## Disciplines that are not obvious from the code

Each of these exists because a simpler approach already failed on real evidence.
Do not remove a safeguard because the happy path looks simpler — check its
history and tests first.

**Fail closed.** If evidence does not establish something, the system says so.
`UNRESOLVED` is a first-class state, not an error. Ambiguity beats fabricated
certainty.

**Never answer by accident.** A parser returning the expected answer is not proof
its reasoning is sound. A CIK appearing in a submission does not make it the
filer; a matching company name establishes nothing; an extractor reporting a
Section 12(b) row does not mean the row exists. Four separate 12(b) failure
shapes are recorded in the evidence corpus, and every one was found by a real run
rather than by the synthetic suite.

**A green check can belong to the wrong commit.** `gh pr checks` returns the
runs it knows about, and for the first ~30s after a push those are the
*previous* commit's. A wait loop that polls for `pending` therefore sees none,
exits immediately, and reports four passes for code that was never built. This
has happened here. **Resolve the SHA and wait on that run**, never on the PR:

```
SHA=$(git rev-parse HEAD)
gh run list --repo <owner>/<repo> --commit "$SHA" --json databaseId,status
gh run view <id> --repo <owner>/<repo> --json conclusion,jobs
```

The tell is a run ID identical to the one from the previous commit. Same class
as the rest of this section: the expected answer arrived, and it was not an
answer to the question asked.

**`MANUAL_VERIFIED` means a *person* read the filing.** A program that reads it
for you has not satisfied it — `src/tradeit/edgar/acquire.py` says so, and
imports nothing from the evidence layer so the boundary is structural. Running
locally does not change this.

**Keep similar concepts apart.** Issuer identity, security identity, ticker
identity, listing venue, filing venue, legal lifecycle, security lifecycle,
mapping quality and adjudication completeness are nine different things. Most of
the schema's complexity is the cost of not collapsing them.

**Measure, do not predict.** Run the command and report what it printed. Several
past errors were predicted counts that turned out wrong.

**Rework decaying tests; do not re-pin them.** When a test fails because the
product legitimately moved, find the invariant it was really asserting and state
that instead. Tests have gone vacuous here twice when a promotion removed the
last instance of the shape under test.

**Preserve quoted text verbatim.** Venue strings, form types and filing language
are recorded exactly as written and never normalised — six distinct Nasdaq
renderings coexist deliberately, and `10-K` is not `10-K405`.

**Never optimise for a milestone count.** FRC is the worked example: rather than
force a CIK onto a bank that files with the FDIC, the identity architecture was
generalised to be regulator-neutral.

**Phase reports are historical records.** `PHASE_0*.md` state what was true at
that phase. Correct current-tense documents; do not rewrite history to match
today.

## Git policy

- Work on the designated feature branch; never push to the default branch.
- Before coding: confirm branch, HEAD, origin, ahead/behind, clean tree.
- After: validate, inspect the diff, commit only intended files, push, and report
  the full commit hash with the sync state.
- **Never** `git reset --hard`, force-push, or rewrite history to make a problem
  disappear. Preserve evidence.
- Do not start the next task automatically because the last one succeeded —
  **except as *Keep going without being asked* above provides**, which supersedes
  this for work already inside an agreed scope. It still binds when the next step
  would cross an approval boundary or enter scope nobody has agreed.
- **Push works from Terminal, over SSH.** An older rule routed all remote
  synchronisation through GitHub Desktop; it is retired. The key was never
  broken — the remote was an HTTPS URL, so Git never offered the key. Check with
  `git remote -v`: `origin` must read `git@github.com:...`. A fresh GitHub
  Desktop clone defaults back to HTTPS and would resurrect the symptom.
  See [`docs/TERMINAL_COMMAND_STANDARD.md`](docs/TERMINAL_COMMAND_STANDARD.md)
  §Git and GitHub.
- A `non-fast-forward` rejection means the remote moved. Integrate it —
  never resolve it with force.

## Environment

| | |
|---|---|
| repo | `/Users/ericsasson/Documents/GitHub/tradeit` |
| venv | `<repo>/.venv` (`tradeit` is **not** installed globally) |
| EDGAR archive | `/Users/ericsasson/Documents/TradeItData/edgar` |
| full index | `<archive>/full-index` |
| filings | `<archive>/filings` |
| diagnostics out | `/Users/ericsasson/Documents/TradeItData/out` |

SEC access needs `EDGAR_USER_AGENT` supplied by the operator as an environment
variable. **Never** hard-code, print, log or commit an API key
(`TWELVE_DATA_API_KEY`, `FMP_API_KEY`, `TIINGO_API_KEY`).

**Do not commit downloaded market data**, diagnostics, temporary extracts or
caches. Never reintroduce an unanchored `data/` ignore rule.

## Constraints on the work itself

- Do not modify `full-01`. It is the frozen Daily machinery baseline.
- Do not alter denominator methodology, detector thresholds, strategy
  parameters, backtesting assumptions or trading logic without an explicit,
  scoped proposition.
- Live trading is disabled by an interlock in `src/tradeit/config.py` requiring
  a machine-local authorisation file. Completing every roadmap phase does not by
  itself authorise it.
- **A result computed on `research-01` may count as evidence — with four
  limitations, by the owner's decision of 2026-09-15** recorded in
  `EDGAR_DELISTING_DENOMINATOR.md` §7e. Measured 2026-09-16, the gate reads
  **`MATERIALLY_SURVIVORSHIP_CORRECTED`** — 11,249 of 21,618 dated Exchange Act
  exits priced (**52.0%**), 91.4% of those whose identity resolves, 30/30
  controls — and the split double count of `RESEARCH_01_DATA_DICTIONARY.md`
  §0.1a is fixed as a read rule. The limitations travel with every result:
  **(1)** coverage is thinnest in the oldest years (25.4% for 1998, 52.9% for
  1999), and history now reaches 1990-01-02 for **survivors only**, so a result
  resting on those years is survivor-weighted by an amount nothing here can
  measure (`RESEARCH_01_DATA_DICTIONARY.md` §0.1b); **(2)** 1,029,023 raw prints
  (2.49%) are withheld before splits still contradicted; **(3)** splits no vendor
  recorded are still not caught, so price levels built on them can be wrong —
  1,807 such splits were recovered from Sharadar on 2026-09-16 and 51.7% of
  securities now carry an action, up from 49.7%; **(4)** every result
  measured before 2026-09-15 ran on double-counted reads and must be re-run
  before it is relied on; **(5)** 1,206 priced exits rest on `vendor_symbol`
  identity from Sharadar rather than a ticker read from a filing.
  **Lifting the rule is not evidence of profitability.** Pre-registration,
  out-of-sample testing and the trial ledger still decide what a result is worth,
  and the rule returns if a later measurement shows the corpus materially worse
  than recorded.
- **Goldbugger is a separate project.** Do not import its gold-specific
  architecture into TradeIt.

## Working with vendors

Standing rules. Current vendor *state* rots and lives in
[`docs/PHASE_06_VENDOR_MATRIX.md`](docs/PHASE_06_VENDOR_MATRIX.md) §1.3 and the
improvement plan's milestone table — read it there, never from this file.

- **Nothing is purchased without the owner's explicit approval, per purchase.**
  Approval of a plan that mentions a vendor is not approval to buy from it.
  Phase 9 is where the money question opens; nothing before it spends anything.
- **The owner sends every vendor email and pastes every reply.** Claude drafts;
  the operator is the only party with a mailbox. A reply enters the repository
  **verbatim or not at all** — three gradings have already been wrong because
  somebody graded a paraphrase, and two of those ran against the vendor.
- **A vendor's assertion is not a measurement.** `VENDOR-STATED` is a real grade
  with real weight on licence and price, which are the vendor's own facts, and
  no weight at all on coverage. Only a file we have loaded and checked moves a
  capability cell.
- **Do not ask a vendor to certify completeness, and do not accept it if
  offered.** Milestone 0a built an EDGAR-derived benchmark to measure coverage
  independently — but read §7be first: it is **blind to exchange delistings
  before roughly 2002**, which is the window `research-01` exists for. In that
  window neither the vendor nor the denominator can answer, and the honest move
  is to say so rather than to substitute one for the other.
- **Licence retention and deletion terms are out of scope entirely.** What a
  vendor requires when a subscription ends is the operator's own decision, taken
  at the operator's discretion. It is not a selection criterion, it does not
  disqualify a vendor, and **no part of this system is designed around it**.
  This rule exists because a version of it was baked into a data contract as an
  absolute constraint and quietly eliminated two candidates; if it reappears
  anywhere, delete it.
- **When a sample file arrives, the identity test can invert.** A control chosen
  because two unrelated issuers held its ticker is testing whether the vendor
  splices them: **a continuous series across the break is the failure, not the
  success.** Read the purpose recorded beside each requested symbol before
  grading any file, and never grade one on whether it "looks complete".
