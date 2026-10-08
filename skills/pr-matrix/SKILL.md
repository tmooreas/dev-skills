---
name: pr-matrix
description: Rank the PRs that are ready to merge so the owner can approve the best ones first. One table, plain language, ordered by importance × work freed × ease of understanding ÷ (size × blast radius × dependence). Use for "pr matrix", "what should I approve", "rank the PRs".
---

# PR matrix

Before starting, bring the skills up to date by running:

    "$(git -C <this dir> rev-parse --show-toplevel)/bin/skills-sync"

It only fast-forwards, is safe beside other running sessions, and prints one line.
If it updated, re-read this file; if it did not, say so in your first line and
continue with the version you have.

The owner approves merges. This skill tells them what to approve first, in a
table they can read without opening any PR.

## Which PRs go in

Only PRs that are ready for an approval right now:

- open, not draft;
- an independent review verdict of PASS at the PR's **current** head (a PASS at
  an older head does not count; a restack or new commit needs its delta check);
- CI green on that exact head.

Everything else goes in a short "not in the matrix" list with the one thing it is
waiting on (review round, fix, owner decision, hold). Check each fact live
(`gh pr view N --json headRefOid,isDraft,state,statusCheckRollup,additions,deletions,baseRefName`)
and against the saved verdict; never carry a status forward from memory.

## Score

```
score = importance × frees × clarity / (size × blast × dependence)
```

Score each factor from the PR's diff, body and review verdict. Be consistent
across the table; the point is the order, not the number.

| Factor | 1 | 2 | 3 |
|---|---|---|---|
| **importance** | internal tidy-up: one home, naming, dev tooling | fixes something a user could hit, or a review/CI gap | closes a product-acceptance gap, or stops a wrong number / wrong chart / silent default reaching the user |
| **frees** | nothing waits on it | 1 PR or lane waits on it (stacked, or conflicts until it lands) | 2+ PRs or lanes wait on it |
| **clarity** | needs the design history to follow | one idea, but spread across stages | one idea the owner can check from the table row alone |
| **blast** | tests, docs, dev tools, eval harness only | one pipeline stage or one report/tab | a gate, a shared helper many stages call, or numbers in delivered output |

- **size** = changed lines (additions + deletions) / 100, minimum 1.
- **dependence** = 1 + number of unmerged PRs it is stacked on.

Recompute after every merge: dependence drops for the PRs stacked on it, and
"frees" drops for the one that merged.

## Output

One table, highest score first:

| # | PR | What it does (plain) | Lines | Score |
|---|---|---|---|---|

- **What it does:** one sentence a non-engineer understands, describing what changes
  for the person running the pipeline or reading the output. No function names,
  no review jargon. Add "(on #N)" when it is stacked.
- **Score:** the number, then the six factors in order, e.g. `2.7 (3·2·3 / 2.2·2·1)`.
  Showing the factors lets the owner disagree with one factor instead of the order.

Under the table:

1. **Merge order** where it differs from the ranking: a stacked PR can rank high but
   still merges after its base.
2. **Not in the matrix:** PR, what it waits on.
3. **Known conflicts** between ranked PRs, and which one has to fix it when it
   lands second.

Read-only. Approvals and merges go through the tracker's merge gate.
