---
name: tracker
description: Track and, when authorized, orchestrate repository work across any coding-agent runtime. Maintain named ownership, bounded implementer/reviewer jobs, current-head evidence and CI, dependency order, and the owner's PASS-only review queue. Use for tracker, sweep, status, claim checking, critical path, agent dispatch, review coordination, or authorized merge coordination.
---

# Tracker

**Fresh skills.** Before running this skill, and before launching any worker that
loads skills, run `"$(git -C <this dir> rev-parse --show-toplevel)/bin/skills-sync"`.
It only fast-forwards (never merges, stashes or discards), takes a lock so parallel
sessions don't collide, and prints one line; if it did not update, say so and keep the
current version. Pulling is safe for running workers (each read its skills when it
started); a worker launched afterwards gets the new text.

## Source of truth and authority

This skill is the canonical orchestration policy, independent of provider,
model, messaging tool or CLI. Runtime mechanics live in
[runtime-adapters.md](runtime-adapters.md); adapters must not weaken these gates.
Project instructions own product contracts, review standards and production
safety. Follow higher-priority instructions and the owner's latest explicit
scope, priorities and deferrals; report conflicts, never silently override them.
Do not maintain competing copies of this policy in launch prompts: reference
this skill and supply only the task-specific contract and runtime permissions.

**Default: observe, do not mutate.** A status/sweep request permits fact gathering,
read-only Git/GitHub inspection, authorized fetches, and local tracker records.
It does not authorize code changes, PR writes, paid extraction, merge or deploy.
An explicit orchestration request permits bounded dispatch and routine handoffs
within its scope; publication permissions must be stated separately.
A skill invocation is not approval of any PR or a production action.

- Owner approval, independent reviewer PASS, exact-head CI, merge readiness and
  deployment permission are separate facts. Never infer one from another.
- Relay approval only for the named PR and covered head. Quote the exact owner
  words, timestamp, source session and transcript entry/path; identify queued
  human messages as queued. A receiver verifies the actual user-authored entry,
  not a pasted quote, keyword hit, tool result or another agent's assertion.
- No authorization to bypass hooks, signatures, required reviews or CI. No
  destructive git cleanup, shared-index manipulation, production writes or
  deployment without the applicable explicit permission.
- One irreversible action per tool call. Approval of a stack tip does not
  approve its dependencies; merge is not deploy.

## Scope and work stack

1. Record every requested PR/task separately, latest priority overrides,
   acceptance criteria, owner approvals and deferrals. Keep one work stack;
   finish the current item or explicitly block it, then advance. A new message
   does not erase outstanding work; an explicit priority override takes effect.
2. Read the project's definition of done and plan of record when applicable.
   Dated measurements are history, not current review or deployment authority.
   A stage-only PR is not a finished customer feature; manual review rules are
   not automated QA.
3. Resolve shared prerequisites inline. Delegate only genuine independent work,
   not top-level planning, simple cleanup or already-known direct questions.
4. Functional/customer correctness, feature completeness and definition of done
   take priority over spend-number precision unless the owner directs otherwise.
   Explicitly deferred cost/accounting/telemetry work stays deferred; do not
   reintroduce its findings as functional blockers. Operational run bounds still
   apply. A real structural dependency on deferred work must be reported; propose
   a functional-only cutover or wait, never silently drop necessary functionality.
5. Greenfield projects carry no backward compatibility with their own past. Task
   packets, implementations and reviews spend no time on shims, aliases,
   fallbacks, legacy readers, dual paths or migrations for the project's earlier
   formats, templates, commands or records. That holds unless a proven, stated
   customer need exists: who depends on the old form, and why they can't move.
   Replace the old path, delete it in the same change and migrate every caller.

## Facts, ownership and ledger

Use live GitHub metadata, exact git refs, signed bodies/trailers, reviewed
comments and saved runtime results. Never invent ownership or repeat a claim
from memory. A title can change; retain stable session/job IDs and source paths.
Unsigned ownership stays unsigned; label transcript linkage as inference.

Maintain one local per-repository/task ledger with:
- task/PR, purpose, dependencies, implementer and independent reviewer identities;
- checkout/worktree, base and exact 40-hex head, model/runtime and role;
- requested permissions, bounds, active job handles and completion state;
- review verdict/head/source, evidence paths, commands/results, CI head and
  required-check outcomes, conflicts and outstanding proof;
- owner approval words/source/covered head, merge/deploy permission separately;
- deferred items, handoffs, delivery/acceptance/refusal, actual usage when reported.

Refresh the ledger on launch, completion, head change, handoff and before delivery.
A stale ledger is not evidence of active work or concurrency. Store UNKNOWN,
NOT_REVIEWED, BLOCKED, PASS and USER_DEFERRED distinctly; a successful process exit
is not a PASS. Report every named PR even when no run was launched.

The existing `scripts/tracker.py` gathers structured facts, not verdict meanings.
Its CLI and Claude-specific storage/session limits are documented in the adapter.
Read actual review text before recording it; an author fix note or simulation
saying "pass" is not an independent verdict. `UNCLEAR` in the legacy script is
not PASS; its inability to store a local verdict does not make that verdict absent.
For non-comment reviews retain the signed/identified report and compare its head
with live metadata. Read verdict-bearing message payloads as well as assistant
text when the runtime supports messages. Do not export unrelated/private payloads.

## Pairing, bounds and async execution

- One implementer and one independent reviewer per task/PR. An existing signed
  author supplies the implementer role for unchanged PRs: no paid no-op author.
  Keep the same reviewer across rounds. A separately requested additional review
  is explicit, not an automatic extra model call. Independent means not the
  implementer, including when both use the same provider/model.
- The orchestrator scopes and integrates; it does not quietly become a second
  code implementer. Disclose any integration-only edit and include it in the
  independent final-head review.
- Reuse existing sessions when useful, not indefinitely growing history. Start a
  bounded CLI job when no suitable live session exists and dispatch is authorized.
  Do not invent a message address or claim a handoff was accepted without evidence.
- Default maximum **two simultaneous model jobs**, including implementers,
  reviewers, meta-auditors and mechanical executors. Tool-only/CI watchers do not
  count. No worker starts nested agents or model calls. Change bounds only with
  explicit task authority.
- Define role, input/read set, allowed writes, exact checkout/head, observable
  acceptance, wall-time limit, output path and stop behavior before launch.
  Model budget flags are request-stop thresholds, not exact billing ceilings.
  Pair runtime time limits with an outer process timeout where available.
- Every CLI worker brief says: never end your turn while any job of yours is
  still running; if the tool backgrounds a command, call wait until it completes.
  Every worker launch with a tool allowlist includes the runtime's wait tool (a
  worker without it exits "waiting" with nothing committed).
- Launch workers detached from the orchestrator (a terminal multiplexer window
  or a detached session), so an orchestrator restart does not kill them. Before
  saying a worker is running or dead, check its process by its session file and
  that file's last write, never a grep of process output; never resume a session
  that may still be live (two workers on one worktree corrupt it).
- Every brief that commits states the commit identity (author and committer);
  rebases never run under a different repo-local user.
- **Every handoff brief is approved by the owner before launch** (owner
  2026-10-06). Show the brief itself: the owner's words it serves, the existing
  code it extends or replaces (file:line), the concrete design, what is out of
  scope, how it is checked, and a line **"Judgments in code:"** listing each place
  the code decides what data means and who should decide it instead ("none" only
  after checking). A BLOCKED review is reported and stops; the next brief waits.
- **Screen every option before the owner sees it** (owner 2026-10-08: "it's a tax
  on me to always be reading these questions and seeing if you're secretly
  suggesting a parser"). Check each option, brief and recommendation against
  review-pr's blocking red flags first (custom parsers, code deciding meaning,
  structure dropped then re-parsed, fail-open, duplicate owners). An option that
  violates one is not offered, not even as the alternative; redesign it or drop it.
  Name the mechanism plainly ("a model with a Python tool checks it", "the producer
  writes a typed field"), never a vague verb ("the grader verifies") that hides
  whether code or a model does the work.
- **Exempt from approval** (owner 2026-10-07): mechanical git with no behaviour
  change (rebases, restacks, their scoped checks, pushes of approved work,
  retargets, CI reruns, closing what the owner already dropped). Do it and report;
  ask only when a conflict needs a behaviour choice. A cheaper model may execute it
  and hands back to an implementer when a conflict touches logic or tests.
- **Smoke before the owner sees it** (owner 2026-10-07): before calling a PR
  ready, the orchestrator runs the changed behaviour on the exact head through
  the project's real entry points, including one refusal or failure path, reads
  the produced artifacts and shows that output. A reviewer PASS and green CI are
  not a substitute.
- Launch independent work asynchronously and continue useful work. Use completion
  notifications/job results, not repeated polling. Wait only when otherwise blocked.
  At a limit, preserve evidence and mark unfinished review NOT_REVIEWED. No
  automatic exhausted-run retry or full re-review of unchanged evidence. A later
  explicitly authorized delta check must state what new evidence it covers.

## Context discipline

- Send a small task packet: goal, relevant decisions, head/base, permissions,
  acceptance, known evidence/blockers and exact paths. Workers start without the
  parent's conversation; include required context, not the whole conversation.
- Use ranges, filtered inventories and bounded output. Logs/diffs/reports stay in
  artifacts; workers read the relevant subset. No giant transcript/JSON bundle in
  a prompt and no private reasoning, encrypted provider data, credentials or
  unrelated tool output in audit exports.
- Account for automatic rules, skills, hooks, plugins and resumed history. A small
  user prompt does not prove a small model context. Inspect actual input,
  cache-creation/cache-read and output counts when the CLI reports them; record
  missing metrics as unavailable. Cached reads are not free. Prefer the least
  ambient context compatible with working authentication and required safety rules.
- No credential/config changes or broken isolated-mode fallback disguised as
  optimization. Avoid environment setup, installs, full suites and paid provider
  calls unless required and authorized. Use the existing interpreter/dependencies.
- Do not repeat sound completed tests/sweeps just to rebuild a report. Read and
  retain their executable method/results, exact revisions and population limits.
  Do not sum cumulative resume costs twice; CLI-reported cost is not invoice spend.

## Review and definition-of-done gates

Apply the project's review drill; do not replace it with this orchestration skill.
Before planning new code, inventory existing capability owners across production
source, including different names, aliases/re-exports and live callers. Every new
helper/predicate/parser/IO adapter must name the existing owner and reuse/removal
or evidence-backed distinction. Same-name repeats are leads, not proof. Scrutinize
JSONL append semantics, parsing and retry wrappers; moved code is not all new code.
Material second-owner/framework debt is not cured by passing numeric tests.

Require observable behavior: reproduce the reported defect before a fix; execute
the changed consumer path after it, then read the actual CLI/UI/artifact output.
Focused tests alone do not prove a customer feature. Preserve product-required
main comparison, boundary/near-miss probes, mutations and historical proof.
For refactors, establish required delivered-byte/behavior equivalence.

A reused historical sweep needs a sound executable method and results for both
versions and the actual selection/population. Identical failures are not proof of
correctness. State exclusions and missing methods. Check units, period/as-of
identity, source/provenance and downstream use, not just numerical equality.
A synthetic boundary scenario must be labeled as such, not a real future event.

- PASS requires all named acceptance and required QA, independently reviewed at
  the exact current head. Read the final verdict, signer, time and head.
- BLOCKED names an observed defect or hard gate with evidence and an actionable
  owner handoff. Missing mandatory proof without an observed defect is
  NOT_REVIEWED, not a fabricated failure or PASS. Explicitly deferred work has
  its own status, not a failed review.
- On publication, compare reviewed head with live PR head and check required CI
  against that SHA. Success on an older head does not count; SKIPPED is not success.
  A head change invalidates the current-head claim until independently checked.
  Mechanical docs/baseline integration gets a scoped final-head check, not an
  unreviewed exemption or repeated whole audit. Runtime conflict resolution needs
  substantive re-review. Never apply labels or narrow tests merely to suppress CI.

## Handoffs and mechanical git

Use the runtime's supported transport: a verified live message address, a scoped
CLI continuation/job, or a saved handoff packet. `SendMessage` is one adapter, not
a requirement. Include recipient identity, task/PR purpose, exact head/base,
verdict and evidence, one requested action, permissions and stop conditions.
Record sent versus accepted/held/refused; a file or PR comment is a handoff record,
not proof that a live worker is fixing it. Report "no fix running" when true.

Routine fixes/rebases/rechecks within authorized scope go to the existing owner,
not back to the human as a review request. Material product tradeoffs and changes
to owner choices go to the owner, batched. Never invent an approval or broaden it.
Prefer direct author/reviewer evidence exchange where available; otherwise share
small artifacts, not full transcripts through the orchestrator.

Use a cheaper **mechanical executor** only for a predetermined, explicitly
permitted git action. It is not a reviewer or substitute implementer. Normally
the author merges; the owner may explicitly delegate mechanical execution.
Run that executor asynchronously, bounded and isolated; a single serial batch
avoids a third persistent per-PR session. Supply an exact PR/head allowlist,
verified approval sources, required PASS/CI evidence, merge order and repository
commands/hooks. The executor rechecks every gate immediately before each action.
Stop on head drift, conflicts, failed/missing checks or authorization ambiguity;
return evidence to the owner/reviewer. No conflict decisions, arbitrary code edits,
force pushes, stash/reset/cleanup, bypasses, auto-merge waiting on future gates or
production actions. Serialize merges; refresh main/dependencies after each and
verify the resulting merge state/SHA. A green CI PR with no required PASS stays held.

## Independent orchestration audit

When requested, run a bounded read-only auditor at meaningful intervals: initial
plan, after a batch (default three completed reviews), and before the owner queue.
Do not pay for duplicate checkpoints with no changed evidence. Supply only new
visible user/assistant decisions, a compact current ledger and prior unresolved
concerns; no entire transcript. The auditor is independent, cannot fix code,
launch agents, change authority or grant PR PASS. Surface every concern with its
resolution/status, including withdrawn claims; disclose its evidence limits.
An exhausted audit is incomplete even if partial feedback was recovered. No
automatic audit retry. Audit work counts toward the model concurrency limit.

## Reporting and merge queue

Lead with customer/definition-of-done movement and critical path, then the
**owner's review queue: only current independent PASS PRs with green required CI
and satisfied dependencies, not already owner-approved**. Give purpose, exact
head, reviewer and brief proof. Reconfirm earlier PASS heads before reusing them.
Already owner-approved PASS PRs belong in the authorized merge queue, not another
review request. NOT_REVIEWED/BLOCKED/deferred PRs appear only as status/holds.

For every requested item report actual checks, omissions, CI/conflicts,
dependencies, named owner and next action. Missing proof stays visible; do not
bury incomplete reviews behind passing test counts. Record unpushed/local work
and source-signature uncertainty. Correct wrong earlier claims plainly.
Never draft approval for work the owner has not reviewed unless asked. Check for
approval already given elsewhere before drafting; verified preapproval persists
within its scope, but never substitutes for a required final reviewer gate.

Merge only the intersection of explicit owner authorization, current independent
PASS, exact-head green required CI, clean merge and landed/satisfied dependencies.
Scope an executor only after that intersection is nonempty. No eligible PR means
report the holds without launching a no-op agent. Deployment remains a separate
explicit action. Manual orchestration discipline is not an implemented automated
QA/merge gate; describe enforcement gaps honestly.

## Maintaining this skill

Keep reusable policy here and runtime details in the adapter. Keep existing
tracker.py structural; changing a prose rule does not add script support or CI
enforcement. For script changes use its behavioral tests/mutants. For skill-only
changes smoke the installed skill loader/prompt builder and documented CLI help,
without a paid model call. Record what actually loaded and any runtime limits.

Project adapters: before applying this skill in a repository, look for
`projects/<repository-name>.md` next to this file and read it. It holds only that
project's specifics (its commands, bounds, model choices, owner rules and
launch recipe); it adds to this skill and overrides it where it says so. Check it
for drift each run (rules here it contradicts without saying so, commands that no
longer exist) and report drift in one line. Generic improvements go in this file,
never in an adapter.

Updated 2026-10-05: provider-neutral bounded CLI orchestration, context discipline,
PASS-only owner queue, independent delta audits and delegated mechanical git.
