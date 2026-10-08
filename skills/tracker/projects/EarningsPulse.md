# tracker adapter: EarningsPulse (ep)

The repository's CLAUDE.md and AGENTS.md are binding and win over this file. This holds
only EarningsPulse's specifics; generic orchestration policy stays in the skill.

## Reviewers and gates

- The reviewer of Claude-written code is GPT/Codex (`omp -p --model openai-codex/...`).
  A Claude or Opus verdict on Claude code is self-grading, not a review; it never goes to
  the owner as a PASS.
- A GPT review runs review-pr passes 1c (second owners) and 1d (extraction antipatterns)
  item by item, plus held-out cases it writes itself. Findings go back to the implementer
  as the broken invariant plus ONE example; the reviewer keeps the rest private.
- Every check a fix adds is labelled STRUCTURE, IDENTITY, CROSS-SOURCE or ACCEPT + MEASURE
  (CLAUDE.md "How a fix may verify anything"). A fix that pattern-matches prose is
  redesigned before the owner sees it, and a GPT reviewer reads each fix spec for
  patches, regex and symptom-fixes before it goes to the owner.
- A finding that survives two review rounds means the approach is wrong: restate the
  complete invariant and rewrite once.
- CI minutes are scarce: run `ep-dev preflight` locally, one PR in CI at a time, and merge
  with `ep-dev merge-when-green N <sha>`.
- Hard cap: 1,500 changed lines per PR (CLAUDE.md; may drop to 800).
- Paid calls (extraction, audits, evals) need an owner-approved cap; free read-only SEC
  GETs need `SEC_USER_AGENT` and at most one request per second.

## The good PR shape (owner-approved examples, 2026-10-07)

Dispatch implementers toward this shape, and check it before calling a PR ready:

1. **Change the existing owner; never add a second rule beside it.** #1272 is one line in
   the existing `_LOWER_IS_BETTER_KEYS` set; #1244 fixes the one guidance formatter;
   #1338's renderer obeys `capital_structure`'s own withholding decision.
2. **Read what the filing declares; don't reconstruct it.** #1364 takes the fiscal key
   from the filing's `dei:DocumentFiscalYearFocus` / `DocumentFiscalPeriodFocus`, only
   when single-valued.
3. **One typed door per staged file, and every reader uses it.** #1316's `RunInputs`: a
   missing file is None, a present-but-invalid one raises. A new direct read of a run's
   staged file is a finding.
4. **Withhold only what is unknown; keep the known figures and say why.** #1338 keeps
   total debt and cash, marks net debt and EV n/a*, and footnotes it.
5. **Small, one concern; a no-byte change proves itself with replay.** #1272 is 16 lines,
   #1244 is 35; #1316 replayed 287/287 runs unchanged.
6. **Check every consumer of a changed output.** #1244 confirmed the downstream parsers
   accept "−" and "to".
7. **Delete dead code you pass.** #1338 removed `_qoq`.
8. **The PR body leads with the behavior change**, quotes the owner, says how often it
   happens on new runs, and files other causes separately (#1364 → #1363).
9. **Search for an existing owner before writing a helper** (`scripts/code_inventory.py`).

## Forward-looking

Design, fix and measure for new runs (owner, 2026-10-07). Historical runs are regression
evidence only: report how a change moves them; never treat preserving or repairing them
as the goal.
