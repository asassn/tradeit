# Terminal command standard

Every Terminal block handed to the operator is run by a human, on macOS/zsh, in a
shell that also holds unrelated work. A block that mangles a URL, silently
selects the wrong candidate, or closes the shell costs a round trip at best and
corrupts evidence at worst. This file exists so those rules are a checklist
rather than something the operator has to restate.

Each rule below is here because it failed once.

## Environment

The operator's paths are fixed and should be written out, not guessed:

```
repo        /Users/ericsasson/Documents/GitHub/tradeit
full index  /Users/ericsasson/Documents/TradeItData/edgar/full-index
filings     /Users/ericsasson/Documents/TradeItData/edgar/filings
```

`tradeit` is **not** installed globally. Invoke it repo-locally:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m tradeit.cli ...
```

**Do not `cd` at all — not even in a subshell.** An earlier version of this rule
permitted `( cd "$REPO" && ... )`, and that permission is withdrawn. Nothing in
this project needs a working directory: `PYTHONPATH` and the interpreter both
take absolute paths, and Python's module resolution does not consult the cwd
when `PYTHONPATH` names the source root. So write

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/ericsasson/Documents/GitHub/tradeit/src \
  /Users/ericsasson/Documents/GitHub/tradeit/.venv/bin/python -m tradeit.cli ...
```

If a directory change ever does become technically unavoidable, say in the
prose why, before the block.

**The data lives outside the repository.** The full index is under
`~/Documents/TradeItData`, not under `~/Documents/GitHub/tradeit`. A block that
looks for `form.idx` by globbing the repository finds nothing and reports "no
local corpus" on a machine that has the entire corpus — a false negative
manufactured by the block itself. Take the index root from the table above, or
accept it as an argument. Never discover it by searching the repo.

**Never paste a path from the assistant's own environment.** The session
container is Linux and its repo sits at `/home/user/tradeit`; the operator's is
macOS at `/Users/ericsasson/Documents/GitHub/tradeit`. A block written against
container paths does not degrade gracefully on the operator's Mac — the
interpreter path does not exist, so nothing runs at all. This has happened
once: a checkpoint block was dry-run in the container, passed there, and was
unrunnable as handed over. Dry-running a block proves it is syntactically sound,
never that it is addressed to the right machine.

## Shell hazards, and why Python is usually the answer

zsh is interactive here, which makes three things fail that would be fine in a
script:

- **`!` triggers history expansion.** A `!` inside double quotes produced
  `zsh: event not found`. Avoid `!` entirely in pasted blocks.
- **`#` is not a comment at the interactive prompt** in every configuration. A
  pasted comment line produced `zsh: command not found: #`. Use `echo` for
  headings instead of comments.
- **macOS `awk` is BSD awk**, not GNU. Multi-line programs, `gensub`, `\s`, and
  several GNU extensions are unavailable, and portable-looking programs have
  failed here.

So: **prefer a single-quoted Python heredoc** (`python - <<'PY'`) for anything
beyond a trivial pipeline. Single-quoting the delimiter stops the shell from
touching the body at all, which removes quoting, `!`, `#` and escaping hazards
in one move. Pass paths in as `argv`, not by interpolation.

`exit` is banned. In a pasted block it closes the operator's shell. Use
functions that `return`, or `if`/`else` structure.

## Selecting candidates

- **If selection is ambiguous, stop and report.** Never auto-pick the first
  match, the highest filing count, or the closest name. An automatic choice
  among several CIKs is how an evidence record gets attached to the wrong
  issuer.
- **Print the candidate set and the selection before acting on it**, so the
  choice is auditable from the output alone.
- **Among annual reports, prefer a primary `10-K` or `10-K405` over a
  `10-K/A`.** An amendment usually restates one item and omits everything else,
  so a search for market or symbol information in an amendment can come back
  empty for a reason that has nothing to do with the question. Reach for the
  amendment only when the amended item is the one being asked about.

## Parsing the EDGAR index

**Do not hand-parse `form.idx` in a throwaway block.** In order of preference:

1. **An accession already established** in this project's record. Re-deriving a
   known accession introduces a chance of getting it wrong for no benefit.
2. **The CLI** — `edgar cik-filings`, `edgar verify-control`, `edgar
   inspect-index` — which run `parse_index`, the reader the corpus audit
   verified field by field against an independent extractor over 27,084,668
   rows with zero mismatches. When filtering its output, match on a
   **substring** (`"424B" in line`) and pull values with anchored regexes
   (`\d{10}-\d{2}-\d{6}`, `\d{4}-\d{2}-\d{2}`) rather than by field position.

   **This applies to the CLI's output too, not only to `form.idx`.** A block
   that correctly used regexes for accession and date still reached for
   `line.split()[2]` to get the form type in an adjacent section — and that
   fails on exactly the forms most worth finding, because `SC 13D`, `DEF 14A`
   and `10-K405 /A` contain spaces. There is no token position that is safe
   across form types. Use a regex for the specific forms being sought
   (`424B[0-9A-Z]*`), or a structured accessor. Never an index into a split.
3. **Raw parsing only when unavoidable**, and then to the format, not to
   whitespace. Two specific traps, both found the hard way:
   - **Never slice the form type from a fixed column** (`prefix[:12]`). The
     column width is an assumption; a longer form type is silently truncated.
   - **Right-anchored `rsplit` is sound for path, date and CIK** — that is the
     production strategy and it survives company names containing spaces,
     ampersands and multi-word form types. What it does *not* handle alone is a
     name wide enough to consume its column padding and run into the CIK
     (`...GENERAL L P5011`); production recovers those from
     `edgar/data/<cik>/`, and an ad-hoc script that just skips a row whose CIK
     field is not all digits will drop filings **silently**. In a search whose
     purpose is to find something, a silent false negative is the worst
     available failure.

## Accessions and URLs

- **Validate before constructing a URL.** An accession must match
  `^\d{10}-\d{2}-\d{6}$` after stripping whitespace. Line-wrap contamination in
  an accession string has already produced malformed SEC URLs once.
- **Strip whitespace and newlines** from any value pulled out of command output.
- **Skip empty values** rather than building a URL with a hole in it.

## The local filing store has a layout — use it

Downloaded filings live at a deterministic path:

```
<filings root>/<cik>/<accession>.txt
```

So an existence check for a known filing is one `Path.is_file()`:

```python
target = FILINGS_ROOT / str(cik) / f"{accession}.txt"
```

**Never answer "do we already have this?" with `rglob` over the filings root.**
A recursive walk of the whole local EDGAR archive to find a file whose exact
path is already known is unnecessary work that grows with the archive and can
take a very long time; it has already made one checkpoint slower than the
download it was guarding. The same applies to listing the root just to show
what is there — enumerate the one CIK directory instead.

Recursive search is for questions where the path genuinely is not known. When
the CIK and accession are both in hand, the path is known.

## Downloads

- Identify with a descriptive SEC-compliant User-Agent including contact.
- Download to a temporary file, then publish. Without this, a truncated file
  looks like a completed download to the next run's skip check.
- **The temporary name must be unique to the invocation**, not a predictable
  shared `<accession>.txt.part`. A fixed name collides with a concurrent run
  and with a stale fragment from an earlier failed one, and cleaning up "the"
  `.part` then destroys somebody else's in-flight download. Use
  `tempfile.mkstemp(dir=target_directory)` so the file is owned by this run and
  lands on the same filesystem as the target, keeping the publish atomic.
- **Delete only the temporary file this invocation created.** Never remove a
  pre-existing temporary file, and never remove or overwrite a completed
  target.
- **Publish only if the fetch returned zero, the temporary file is non-empty,
  and the target does not already exist.** A zero-length success is a failure
  with better manners, and an existing target means another run got there
  first — leave it alone and report it rather than racing it.
- **A successful fetch is not a validated one.** "curl returned zero and the
  file is non-empty" says the transfer worked, not that the right document
  arrived. Before accepting a download as evidence, parse its SGML header and
  check it against what was asked for, at minimum:

  ```
  ACCESSION NUMBER            matches the requested accession
  CENTRAL INDEX KEY           matches the requested CIK
  CONFORMED SUBMISSION TYPE   matches the expected form
  FILED AS OF DATE            matches the index
  ```

  Rename the temporary file only after those match. A mismatch means the URL,
  the index row or the assumption behind them is wrong, and analysing the file
  anyway attaches evidence to the wrong filing — the failure this project has
  worked hardest to prevent.
- **Parse the header with `tradeit.edgar.submission`, not a fresh regex.**
  `parse_submission_header`, `split_documents` and `header_mismatches` are
  tested; a block that re-derives them is not. This rule exists because the
  ad-hoc version captured the submission type with `\S+`, which stops at the
  first space: `DEF 14A` became `DEF`, and the gate refused a filing that was
  exactly the one requested. Four of thirty-four stored submissions carried the
  same truncation — `POS AM`, `PRE 14A`, `DEF 14A`.

  The prohibition on `line.split()[n]` for a form type was already written down
  here, and `\S+` re-broke it through a different construct. **Any first-token
  read of a form type is the same bug**, whatever syntax reaches for it. The
  forms that expose it are the multi-word ones — `DEF 14A`, `PRE 14A`,
  `POS AM`, `SC 13D`, `SC 13G` — and a pipeline that has only ever validated
  `10-K`, `10-Q` and `8-A12B` has not been tested against it at all.
- **Compare the form by exact normalised equality, not by prefix.**
  `t.upper() == "8-K"`, never `t.startswith("8-K")` — the prefix test silently
  accepts `8-K/A`, and an amendment usually restates one item and omits the
  rest, so it answers a different question than the one asked. If amendments
  belong in the set, declare them in the set explicitly.
- Apply the same check to a file that is **already present**. If an existing
  local file fails header validation, **stop and report it. Do not delete it
  and do not overwrite it** — it may be evidence of how the wrong file got
  there, and destroying it destroys the trail.
- **Normalise the CIK before comparing it.** The header writes it zero-padded
  to ten digits (`0000072859`); the index and our records write it bare
  (`72859`). Compare as integers. A string comparison fails on every filing and
  looks like a header mismatch.
- **Collect every `CENTRAL INDEX KEY`, not the first.** A submission can carry
  several FILER blocks — co-registrants on an S-4, a parent and a financing
  subsidiary — and validating against the first one silently accepts a filing
  belonging to a different registrant. Check the expected CIK is *among* them,
  and print the names so co-registration is visible rather than inferred.
- **A validated header identifies the file, not the speaker.** In a
  multi-registrant submission, the presence of a CIK in the header does **not**
  attribute any body sentence to that registrant. Attribution has to come from
  the text: which entity is named, what tense is used, and what the document
  says each party is. Enron's 1996 reincorporation filing carries both the
  predecessor and the newly formed successor as filers, and states one's stock
  "is traded" while the other's "will be listed" — the tense, not the header,
  is what separates them.
- Skip files already present, non-empty **and header-validated**.
- Report SUCCESS / FAILED / SKIP / HEADER-MISMATCH per accession.
- **Baseline `curl` flags**, all of them, every time:

  ```
  --silent --show-error --fail --location --compressed
  --connect-timeout 20 --retry 3 --retry-delay 2 --max-time 300
  ```

  `--show-error` because `--silent` alone swallows the reason for a failure and
  leaves only an exit code. `--location` because a redirect otherwise lands a
  redirect stub in the `.part` and looks like a short download. `--connect-timeout`
  because `--max-time` alone lets a stalled connection consume the whole budget
  before the first byte arrives.
- Say in the surrounding prose **exactly which files a block may download**.
  A block that touches the network must never look read-only.
- Prefer `subprocess.run([...])` with a list of arguments over a shell string:
  no quoting, no word splitting, no interpolation.

## Search-scope honesty

**Absence in locally downloaded filing bodies is not absence from the SEC
corpus.** A search over four downloaded files out of 360 indexed filings
establishes nothing about the other 356, and reporting it as "not found" without
that qualifier has already produced a wrong conclusion once.

Every negative finding must state its scope, using these terms:

| scope | means |
|---|---|
| exhaustive local-index search | every row of every `form.idx` in the local corpus |
| local filing-body search | only the filing bodies already downloaded, named |
| individual manual read | one document, read by a human |
| inference | not evidence; label it and justify it |

### A result is only what the output shows

**Never report on a section of a command whose output you have not seen.** "The
block completed successfully" means it ran. It does not say what any search
found, and a multi-part block routinely returns one part to the reader and not
another — scrolled off, truncated, or simply not pasted.

If a block had five sections and three were relayed, the other two are
**undetermined**, not passed and not failed. Say so and ask for them. Inferring
a section's result from the block exiting cleanly, from the parts that were
relayed, or from what the answer "should" be is fabrication with a procedural
alibi — and it is indistinguishable, in the written record, from having actually
checked.

### Prefer what happened over what was going to happen

**A post-effective filing describing an event in the past tense beats chaining
prospective terms through an intervening amendment.** Registration and proxy
materials state what *will* occur; anything filed between them and the closing
can change it. A chain of "terms said X" plus "the deal closed" is only as good
as the assumption that nothing in between touched X — and that assumption is
exactly what a reader cannot verify.

A filing made after the event, describing it in the past tense, is downstream of
every amendment. It does not answer the intervening-change question; it makes it
irrelevant. Look for that document first.

Worked example: Enron's Reincorporation Merger. The 1996 registration materials
said each share *will be* converted one-for-one, but a First Amendment dated
1997-04-14 sat between them and the 1997-07-01 effectiveness. The POS AM filed
1997-07-11 states that as a result of the merger each issued share of Old Enron
common *was converted into* one share of New Enron common — and the amendment
question stopped mattering.

### Search for the specific document, not the generic phrase

Two failures from the same investigation:

- **Broad survival-clause searches produce noise, not evidence.** "remains in
  full force and effect" and "all other terms" appear in every unrelated
  contract and exhibit in a large filing. Searching for them without transaction
  context returns hits that look responsive and are not.
- **Prefer a named exhibit or file number over a keyword.** `EX-3.02 ARTICLES OF
  MERGER`, or Registration No. `33-60417`, identifies a document; "amendment"
  identifies a word. When a filing's own inventory or a cross-reference names
  the operative document, go to it directly rather than grepping toward it.

### Relevance before exhaustion

**Document relevance must be established before a negative document result can
be treated as evidentiary exhaustion.**

A filing only spends an evidentiary attempt if the fact being sought is one that
filing would reasonably be expected to state. A standalone debt prospectus that
never mentions the common stock has not been *asked* whether the issuer's common
trades under a given symbol, and its silence says nothing about the answer.
Counting it as a failed attempt conflates "this document did not answer" with
"this document answered no", and burns a search budget on a document that was
never a witness.

So, in order:

1. **Classify what the document is about** — which security, which section, what
   the filing exists to disclose.
2. **Only then** apply the acceptance criterion.
3. A **non-probative** document is recorded as inspected and not probative. It
   does not count toward exhaustion, and its silence is never cited as evidence
   against the proposition.

This is an applicability screen, not a softening of the acceptance standard. A
probative document that is silent is a real negative and must be recorded as
one.

Worked example, from the ENE successor-ticker search: Item 5 of a 10-K is
literally "Market for Registrant's Common Equity", and a proxy statement carries
shareholder and common-stock information — both are probative, so their silence
counted. A `424B` covering a note issue would not be, and its silence would not.

## Output

Bounded. Cap matches per file and characters per match; a block that prints a
whole 10-K is unusable. End every substantial block with an explicit completion
marker, e.g. `===== <NAME> COMPLETE =====`, so a truncated paste is obvious.

### An intentional stop must exit non-zero

**`raise SystemExit` with no argument exits 0.** A block that refuses a
mismatched header, aborts on a failed fetch, or stops because a derivation
disagreed with its cross-check therefore reports *success* to the shell — and
to anything reading `$?`, a CI wrapper, or an `&&` chain. Every refusal branch
must carry a status:

```python
raise SystemExit(2)
```

Normal completion falls off the end of the script and stays 0. Nothing else
changes.

The completion marker and the exit status are complementary, not redundant. The
marker tells a human the paste is whole; the status tells the shell whether the
work actually happened. A stop shows neither the marker nor a zero status, so
the two together make an intentional abort impossible to mistake for a pass.

## Read-only vs network

State which one a block is, in the prose, before the block. A read-only block
should contain no `curl` at all.

## Git and GitHub

**Terminal Git is local and read-only. Remote synchronization happens in GitHub
Desktop.**

GitHub HTTPS authentication does not work from Terminal on the operator's Mac.
Any command that reaches the remote — `git fetch`, `git pull`, `git push`,
`git ls-remote`, `git clone`, `git remote update` — stops at an interactive
`Username for 'https://github.com':` prompt and hangs the block. There is no
credential to type, so the block cannot succeed; it can only be interrupted.

So a Terminal block may use, freely:

```
git status, git log, git show, git diff, git branch, git rev-parse
```

and any other command that reads only what is already on disk. It may also
commit locally when the operator has asked for a commit.

It may **never** contain a networked Git command. When remote state needs to
change — pushing a commit, picking up a branch — say so in prose and let the
operator do it in GitHub Desktop. When remote state needs to be *read*, ask the
operator for the value rather than fetching it.

This restriction is lifted only when the operator states explicitly that CLI
authentication has been repaired. Until then it holds regardless of how
convenient a one-line push would be.
