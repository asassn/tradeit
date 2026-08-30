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
3. [`docs/EDGAR_DELISTING_DENOMINATOR.md`](docs/EDGAR_DELISTING_DENOMINATOR.md)
   §4 — the identity and evidence rules. Normative.
4. [`docs/adr/`](docs/adr/) — 26 decisions that constrain later phases.

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
`packages = ["tradeit"]`, and both report the same 164 files. CI adds coverage
flags to `pytest`, which change no result.

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
- Do not start the next task automatically because the last one succeeded.
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
- **Goldbugger is a separate project.** Do not import its gold-specific
  architecture into TradeIt.
