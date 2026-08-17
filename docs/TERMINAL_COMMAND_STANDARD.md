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

Never change the operator's working directory permanently. Use a subshell
(`( cd "$REPO" && ... )`) where a directory change is needed at all.

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

## Downloads

- Identify with a descriptive SEC-compliant User-Agent including contact.
- Write to `<name>.part`, rename only after the fetch succeeds, and delete the
  `.part` on failure. Without this, a truncated file looks like a completed
  download to the next run's skip check.
- Rename only when the fetch returned zero **and** the `.part` is non-empty. A
  zero-length success is a failure with better manners.
- Skip files already present and non-empty.
- Report SUCCESS / FAILED / SKIP per accession.
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

## Output

Bounded. Cap matches per file and characters per match; a block that prints a
whole 10-K is unusable. End every substantial block with an explicit completion
marker, e.g. `===== <NAME> COMPLETE =====`, so a truncated paste is obvious.

## Read-only vs network

State which one a block is, in the prose, before the block. A read-only block
should contain no `curl` at all.
