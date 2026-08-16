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
- Skip files already present and non-empty.
- Report SUCCESS / FAILED / SKIP per accession.
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
