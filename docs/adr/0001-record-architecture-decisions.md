# ADR-0001: Record architecture decisions

**Status:** Accepted · **Date:** 2026-08-07 · **Phase:** 1

## Context

This platform will be built over many phases, and later phases will be tempted
to undo earlier constraints — particularly the ones that make backtests slower
or more pessimistic. "Why can't I just read the latest price?" is a question
that will be asked repeatedly, and the answer needs to be written down once
rather than re-argued each time.

## Decision

Every decision that constrains future work is recorded as a numbered ADR in
`docs/adr/`. ADRs are immutable once accepted: a reversal is a new ADR that
supersedes the old one, so the reasoning trail survives.

An ADR is warranted when a choice (a) is hard to reverse later, (b) will look
arbitrary to someone who did not participate, or (c) trades a visible benefit
(speed, returns, simplicity) for an invisible one (correctness, auditability).

## Consequences

- Reviewers can check a change against the constraint it might violate.
- Some ADRs will eventually be superseded. That is the mechanism working.
- Routine choices — library versions, file layout — stay out of ADRs.
