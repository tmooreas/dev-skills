---
name: issue-queue
description: Reconcile open issues and active PRs with current code and owner priorities, rebuild a working issue map, and recommend the next action through a complete customer outcome. Verify incidents before recommending fixes or closing issues. Use for issue-queue, backlog triage, or deciding what to work on next. Selection does not authorize implementation or issue writes.
---

# Issue-queue triage drill

Before starting, bring the skills up to date by running:

    "$(git -C <this dir> rev-parse --show-toplevel)/bin/skills-sync"

It only fast-forwards, is safe beside other running sessions, and prints one line.
If it updated, re-read this file; if it did not, say so in your first line and
continue with the version you have.

The deliverable is one recommended next action, its remaining dependency sequence
and a current working issue map. Include independent work only when useful; don't
fill a quota or begin implementation without authorization.

Read the project's instructions, agreed product goal, architecture plan, review
standards and latest owner decisions/deferrals. Use [review-pr](../review-pr/SKILL.md)
for the shared seven questions and evidence standard; do not maintain a second
checklist here. Use [tracker](../tracker/SKILL.md) for dispatch, role bounds,
PASS-only owner queues and merge authority. Neither issue selection nor a skill
invocation approves a PR, source change, deployment or production action.

Keep the skill repository agnostic. Product deadlines, framework epic/slice IDs,
source-provider restrictions, complexity limits and production commands come from
the target project. Old consolidated scopes and owner decisions guide sequencing;
they do not prove an incident still exists or that code reached production.

## 1. Pull a complete inventory

Resolve the target repository from the checkout's origin or explicit task scope,
not from a hardcoded repository name. Read open issues and PRs, all pages:

```sh
gh api --paginate 'repos/<owner>/<repo>/issues?state=open&per_page=100'
gh api --paginate 'repos/<owner>/<repo>/pulls?state=open&per_page=100'
```

The issues endpoint also returns pull requests; exclude rows with `pull_request`
from the issue count. Use the available GitHub API/client and inspect read errors.
A failed page makes the inventory incomplete; retry a transient read within task
bounds, then report the missing portion instead of inferring completeness.
Leave automated dependency PRs alone unless the owner includes them.

An issue with an open implementing PR is **in flight**. The owner reviews PRs on
their schedule; don't make the next backlog action "review/merge this PR" merely
because it is open. The exception is a specific merge blocking specific work:
name the PR, dependency and mechanism. Do not send unreviewed/blocked PRs to the
owner as review requests; tracker owns that queue. Inspect in-flight changed files
before recommending overlapping work; don't take over another agent's branch.

## 2. Rebuild the derived working map

GitHub is the issue-state source of truth; the map is derived, not a replacement.
Use the project's agreed local map (often an untracked `ISSUES.md` in the task
worktree). If no such convention exists, use a named local task artifact instead
of overwriting tracked documentation or changing .gitignore without authority.

Include:
- dated header, exact main commit, inventory completeness, open/in-flight counts;
- one table per customer outcome, with existing code owners and dependency order;
- columns: outcome/issue, customer risk, code owner, state/PR/contributor,
  dependency/next action, completion proof;
- states: open, in-flight, blocked-on, needs-owner-decision, measure-first,
  superseded (with surviving work), or explicitly owner-deferred;
- unchecked items marked unverified, not silently classified as fixed;
- preparation/staging, production integration, customer correction and deployment
  distinguished; one outcome does not require one large PR.

If the project has an active architecture/framework program, include its epic and
slices as a dependency-ordered table with actual merged/in-flight state and the
next unblocked slice. Do not invent a program or copy another project's issue IDs.
Similar filenames are not proof of a shared cause; a signed owner and cited code
matter more than a topical grouping.

## 3. Select the next action

Rank demonstrated harm, contribution to the agreed goal and dependency value.
Price integration and proof through the customer boundary, not just the next
small diff. Use implementation/review cost to break ties, not to substitute an
easy unrelated patch for an important outcome.

- **Finish the outcome.** Prefer completing a verified repair, including the
  missing consumer/integration step. A preparatory artifact is not the delivered
  correction. Don't preserve a bad design only because work already started.
- **Apply explicit priorities/deferrals.** Functional correctness, feature
  completeness and definition of done outrank spend-number precision unless
  directed otherwise. Deferred accounting/telemetry is not a functional blocker.
  A structural stack on deferred work still needs a disclosed cutover or wait.
- **Urgent interruptions need evidence.** Wrong customer content, delivery failure,
  data loss, security or truly runaway operational spend can interrupt the plan.
  State what is displaced. Don't recast deferred spend-accounting precision as an
  urgent budget failure.
- **Root cause over patches.** Fix the owning decision rather than add another
  guard/exception/regex beside it. Name the weaker rule being removed. A narrow
  refusal may be safe containment but is not the final repair when required
  evidence exists. Treat recurring extract-then-discard failures as a mechanism,
  not isolated symptoms.
- **One owner per decision.** Consolidation beats a second parser/resolver/config
  owner. Structural checks belong in the existing gate/validator, not a new prose
  detector. Unknown identity is not proved by a plausibility band or default unit.
- **Measure first when needed.** Prefer executable incident repros and sound
  population evidence. Current source policy, units, period/as-of identity and
  actual input shapes govern the recommendation, not assumptions from an old case.
- **Price the landing site.** Inspect relevant complexity/layer/size ratchets and
  target architecture. A high-risk function needs stronger preservation proof,
  not an arbitrary automatic rejection. Split by concern/dependency, not a quota.
- **Recurrence matters.** A previous fix causing another defect in the same owner
  warrants checking shared callers and the invariant. It does not automatically
  authorize a broad refactor.
- **In-flight dependencies are real.** Don't build on unmerged prerequisites
  without permission. Name the block, choose truly independent work if useful,
  and flag pending architectural moves that would otherwise undo the patch.
- **Framework work is a candidate when it is the owner's focus.** Choose the next
  unblocked slice when customer work is blocked/in flight. Enforce the project's
  behavior-preservation requirements for pure moves and separate customer behavior
  changes from neutral structure changes. Respect its allowed in-flight count.
- **Deprioritize theoretical cleanup.** A trigger already structurally impossible,
  speculative experiment or broad cleanup needs a demonstrated dependency before
  it displaces a verified customer outcome.
- **Source rights remain separate.** Apply owner source-provider choices; reliability
  does not grant commercial fetching, redistribution or paid API permission.

Ask only material owner choices blocking the recommended next work. Tool/repo
facts are investigated, not delegated back to the human. Distant decisions stay
attached to their issues, not a questionnaire.

## 4. Verify the picks on current main

Issues go stale in both directions: fixed-but-open and open-but-misdescribed.
Before a recommendation is final:
1. Read the full issue body and all comments; a comment may contain re-diagnosis.
2. Establish current main (authorized fetch if needed), cited owning symbols and
   live callers. Use available LSP definitions/references; line numbers can drift.
3. Inspect intervening commits touching the mechanism since the incident.
4. Compare saved artifact age/version with merges and note hand-heals. A healed
   file proves neither original code correctness nor the new path's repair.
5. Execute the incident reproduction where safely authorized, through the boundary
   at issue, with real inputs in an isolated copy. Include prior values as well as
   current values when temporal identity matters.

Treat the user's reported incident as evidence. Establish whether it remains on
main and what the candidate adds; do not claim the report was wrong just because
a later fix or healed artifact no longer fails. If the original failure is already
fixed, identify the fixing commit and reassess latent protection/contract work
before recommending another implementation or review round.

If reconstruction is needed, disclose the exact failing input rebuilt in memory
or an isolated copy. If proof is unavailable, mark it unverified/measure-first;
do not endorse a speculative fix. Triage does not authorize paid probes,
production writes, subscriber sends, destructive tests or environment installs.
Report the precise missing authority/prerequisite and finish reachable work.

## 5. Re-diagnosis and no false closures

Issue maintenance requires explicit authority. In read-only mode propose changes
without posting or closing. For a re-diagnosed issue, name the actual mechanism
and evidence; preserve distinct acceptance criteria. For duplicates, reference
both issues and the surviving work. Superseded/duplicate is not the same as fixed.

A **fixed** closure requires BOTH in its closing evidence:
1. The defect's own repro executed on current main now gives the required result,
   at the affected boundary. Extraction may end at staging; a wrong rendered
   figure requires the actual render path. Use isolated copies of real artifacts.
2. The fixing commit/PR identified by hash/number.

A disappeared line, related-looking merge, green suite, hand-healed artifact or
confident reasoning is insufficient. Adjacent fixes can address different
mechanisms. Partial proof means leave the issue open and explain what remains.
A preparatory PR can be complete while the parent incident stays open; merged code
is not evidence that production deployment repaired the customer outcome.

When authorized, one update/closure per tool call; write the executable proof in
the comment, not only in a private artifact. Do not silently convert a triage
request into implementation, closure, PR approval or deployment authority.

## 6. Revise and report

If verification changes the recommendation, reassess the sequence; don't fill
space with unrelated work. Report in plain language:
- next action: customer outcome, issue, existing code owner, why now and exact step;
- remaining dependency sequence and completion proof through the customer boundary;
- independent work/urgent interruption only when justified;
- blocking merges only where the mechanism blocks named work;
- deferred/in-flight work and why it should not start again;
- authorized closures/updates with executed proof, or proposed changes if read-only;
- verification limits and only material owner decisions blocking this action.

Name the assigned contributor/session where verified; unknown ownership stays
unknown. Selection is the deliverable. Building starts only within separately
authorized scope.
