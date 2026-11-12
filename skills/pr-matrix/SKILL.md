---
name: pr-matrix
description: Rank the PRs that are ready to merge so the owner can approve the best ones first. One table, plain language, ordered by importance² × work freed × ease of understanding ÷ (size × blast radius × dependence). Use for "pr matrix", "what should I approve", "rank the PRs".
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

- open (a draft counts; PRs stay draft until merge, owner 2026-11-03);
- an independent review verdict of PASS at the PR's **current** head (a PASS at
  an older head does not count; a restack or new commit needs its delta check);
- CI green on that exact head;
- every PR it is stacked on is itself in the matrix (owner 2026-11-02). A PR whose
  base is still in review or rework waits in "not in the matrix" with its base.

Everything else goes in a short "not in the matrix" list with the one thing it is
waiting on (review round, fix, owner decision, hold). Check each fact live
(`gh pr view N --json headRefOid,isDraft,state,statusCheckRollup,additions,deletions,baseRefName`)
and against the saved verdict; never carry a status forward from memory.

## Score

```
score = importance² × frees × clarity / (size × blast × dependence)
```

Score each factor from the PR's diff, body and review verdict. Be consistent
across the table; the point is the order, not the number. Never work the number out by
hand: write the factors, then compute every Score cell with code (owner 2026-11-09, the
hand-worked scores were wrong).

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

One table in merge order: merge order beats score (owner 2026-11-09). A PR that has to
land before another ranks above it, whatever the scores. That covers a stacked PR's base
(owner 2026-11-02), a known conflict whose fix is assigned to the later PR, and any
declared order (e.g. "merge #499 with #510"). Among PRs free to go in any order, higher
score first. Every PR waiting on the owner is in it: no second "also ready" table (owner
2026-11-02, "all ready prs should be in one list ordered by pr matrix criteria").
Stacking shows in "(on #N)" and dependence. An approved PR leaves the table (owner
2026-11-09, "theyre beyond me so dont need to be there anymore"). One line above the
table, "Approved, merging (nothing needed from you)", lists them in merge order with
what each waits on. A PR that needs a re-look after a rebase goes back into the table
only once it is ready to act on; a PR the owner should not act on yet never sits in the
table (owner 2026-11-09, "dont put it where stuff im supposed to approve goes").
The tracker merges in that order; an approved PR waits behind an unapproved one it
conflicts with or depends on.

| # | PR | What it does (plain) | Lines | Score |
|---|---|---|---|---|

- **What it does:** one sentence a non-engineer understands, describing what changes
  for the person running the pipeline or reading the output. No function names,
  no review jargon. Add "(on #N)" when it is stacked.
- **Score:** the number, then the six factors in order, e.g. `2.7 (3·2·3 / 2.2·2·1)`.
  Showing the factors lets the owner disagree with one factor instead of the order.

Under the table:

1. **Your decisions, in order:** the owner's open questions from the tracker's worklist,
   one line each, short ones to answer in chat, bigger ones as briefs (owner 2026-11-09:
   "keep the decisions flowing to me").
2. **Parked by you:** what the owner parked; it is not asked again.
3. **In progress (no decision needed):** the worklist items lanes are working on, in
   plain words.
4. **Merge order** for each stack or conflict chain, matching the table.
5. **Not in the matrix:** PR, what it waits on.
6. **Known conflicts** between ranked PRs, and which one has to fix it when it
   lands second.
7. **Merged today:** each PR linked, with a few words on what it did.

Read-only toward the repository. Approvals and merges go through the tracker's merge gate.
After sending, post the same text with
`<repo>/bin/owner-dash --project <repository-name> put matrix -`; the owner reads it at
`http://localhost:8765`. Repost it whenever a PR becomes ready for approval, is approved,
merges or goes on hold, in the same turn as the chat update (owner 2026-10-29: "this is
the new system"). Every PR with a posted walkthrough sits in a table row, even while it
finishes checks, with its status in the row: the dash attaches walkthroughs to table rows
only (owner 2026-10-29: "368 is not on the dash yet").
`bin/dash_matrix.py` edits a posted matrix in place: `put` renumbers the rows, refuses a
score-order break (rows whose text says `at the bottom (your call)` sit last by the owner's
choice), and reposts; `relook N SECTION0_FILE BASE` prepends a section 0 to a posted
walkthrough and reposts it with its attachments. One PR per row (owner 2026-11-11).
