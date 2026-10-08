---
name: review-pr
description: Independently review a PR to a merge-decision standard. Check existing ownership, current-main behavior, customer-path proof, simplicity and architecture; execute focused tests, mutations, boundary probes and sound historical comparisons as applicable. Return exact-head PASS, BLOCKED or NOT_REVIEWED with executable evidence and a net-value call. Use for review-pr, review N, or final PR review.
---

# PR review drill

Review is a gate, not a polish: does this belong in the repository in this shape?
Correctness alone does not justify needless ownership duplication or machinery.
The deliverable is an independent exact-head verdict the owner can act on, a
complete finding list with evidence, and a net-value call: worth it, worth it with
a change, or not worth the burden.

Before starting, bring the skills up to date by running:

    "$(git -C <this dir> rev-parse --show-toplevel)/bin/skills-sync"

It only fast-forwards, is safe beside other running sessions, and prints one line.
If it updated, re-read this file; if it did not, say so in the verdict's first line and
continue with the version you have.

Read the target project's AGENTS.md, CLAUDE.md or equivalent instructions,
architecture contracts, acceptance criteria and review rules first. Product
commands, source policies, complexity/size limits and production permissions
come from that project, not from the repository this skill originated in.
Use [tracker](../tracker/SKILL.md) for orchestration bounds, handoffs and approval
queues; this skill owns review rigor, not dispatch or merge authority.

## Authority and blocking bar

Default review is read-only toward source, git index and GitHub. Use isolated
copies/worktrees and existing dependencies for execution. Read-only git and
GitHub metadata are permitted; distinguish an authorized fetch from source
writes. Do not abbreviate the restriction to "no git". No production writes,
paid provider calls, installs, full suite on a shared production box, source
fixes, pushes or merges without separate authority. A report may be written to
the assigned artifact. Post the verdict only when publication is authorized.

A demonstrated defect blocks when it can reach customers, delivery, security,
money or data, or adds material architecture/second-owner debt under project
rules. Apply the owner's latest scope and explicit deferrals: deferred accounting
or telemetry findings are not functional gates. Missing required proof is
NOT_REVIEWED, not an invented defect or PASS. Lesser observations are notes with
named follow-ups, not extra scope quietly added to the PR.

Price rigor by blast radius. Runtime/customer changes get the applicable passes
below. Docs-only changes get context and content/link verification, not a full
adversarial pipeline audit. Keep one independent reviewer across rounds; default
two rounds (initial review plus scoped fix verification), subject to explicit
project/task bounds. If the approach still violates the invariant, stop patching
and return the full invariant/finding list; never automatically spin more rounds.

## 0. Context before code: seven questions

Open the verdict with concrete answers:
1. Is the proposed behavior right for the customer, not just the ticket?
2. What already owns this decision, identity, calculation or configuration?
3. What weaker or duplicate rule does this replace? If it adds a second owner,
   identify that debt rather than treating it as inevitable.
4. Does the original incident still exist on current main? Identify intervening
   fixes; do not mistake a hand-healed artifact for code-path repair.
5. What actual consumer-path reproduction proves the change?
6. Is there a simpler shape? Name what it removes and the tradeoff. Separate
   new logic from lines only moved; moving code is not all new design.
7. Does it fit the project's target architecture? Classify target shape,
   neutral existing debt, or new debt the target must undo; cite the contract.

Read the full PR body, linked issue and all review/decision history. Match every
acceptance criterion. Fix verification starts with prior findings. Verify
implementer identity from project signatures/trailers or other source evidence;
never independently review your own implementation. Unknown ownership stays
unknown, with inference labeled.

Pin the exact 40-hex head and current main/base. Inspect every hunk and enough
surrounding module code to understand siblings and consumers. Fetch only when
permitted; use a detached isolated worktree if execution requires one. Large
PRs/complexity limits are evaluated against project policy, not a universal
line-count threshold. Record process exceptions rather than inventing them.

## 1. Ownership, architecture and reader burden

Before proposing new helpers, inventory capabilities across the whole production
tree. Use the project's inventory tool when present, then semantic searches for
different names, aliases/re-exports and live call sites. Symbol definitions and
references use available language-server tools; text search alone is not code
intelligence when an LSP is available.

For every NEW helper, predicate, parser or IO adapter record:

`helper -> existing owner (path:line) -> reuse / remove / distinct because evidence`

- JSONL append helpers: compare append mode, encoding, serialization, newline
  and error behavior. Parse/retry wrappers: compare inputs, failure modes,
  retry authority and side effects; billing semantics only when in scope.
- A typed domain boundary owned in one place is not a copied utility. A moved
  helper is not an added one. Distinguish preexisting debt from debt introduced
  by this PR. Repeated names are candidates, never automatic blockers.
- A material second owner can block even when today's numerical output is right.

Check the project's typed artifact loaders, status/gate registries, forward
import/layer contract, ratchets and owned modules. New untyped consumers beside a
typed loader, imports of private names, backwards dependencies, exemption growth,
raw status branching or manually threaded/re-looked-up state need concrete
ownership justification. A split at an arbitrary line count is not a module
with a concern. Do not apply EarningsPulse-specific registries or bans to a
project that has not adopted them.

Complexity is a reader/regression-risk signal. Explain where a new decision
belongs; do not require abstraction just to improve a metric. Apply the project's
comment/docstring and file-size rules without mistaking cosmetic preference for
a customer defect. Once an automated lint enforces a rule, use its actual result;
a manual review checklist is not proof that automated enforcement exists.

## 2. Reachability and promise surfaces

Locate actual callers of every new/changed entry point and inspect their inputs.
An optional parameter never supplied, a disabled feature or an unconsumed field
can make passing helper tests irrelevant. Check effective configuration only
through permitted read-only surfaces; if production inspection is unavailable,
state the gap instead of assuming the default is deployed.

Claims such as "only", "sole", "every" and "never" require whole-production
search, not just changed files. Follow data through its typed boundary, source
selection, calculation and consumer. Verify units, period/as-of identity,
provenance/source labels, and failure/refusal semantics, not just the value.
Recording provenance does not repair a downstream consumer that ignores it.

Inspect touched schemas/contracts, agent skills, decision logs, CLI/API promises,
migration chains and live callers. Required documentation/callsite changes land
with the change. For in-scope paid calls, check worst-case attempts and operational
bounds; respect explicit deferral of spend-accounting correctness.

## 3. Current-main and customer proof

A user-reported incident is evidence; check whether it remains on current main
before claiming new code repairs it. Execute the incident's real failing path,
not a helper with inputs production never passes. Compare old/new behavior in
separate worktrees or isolated in-memory modules, never by overwriting shared
source with `git checkout <ref> -- <path>`.

Use real cached/staged inputs where available, offline and in copies. If an input
was hand-healed, reconstruct the documented failure in memory/an isolated copy
and disclose it. Name the fixing commit if another change already repaired the
incident; reassess the proposed PR's incremental value before another round.

Run the actual CLI/service consumer or UI and observe its output. A deliverable
change requires the real builder and output readback/visual inspection appropriate
to the surface. A builder that omits the affected slide/field is not acceptance
proof. Label synthetic boundary scenarios as scenarios, not observed future events.
For a pure refactor establish the required behavior/delivered-byte equivalence;
a helper test or successful render alone is not the comparison.

## 4. Focused batteries, mutations and near misses

Run relevant existing focused suites once after changes settle. Inspect deleted
tests for lost behavioral coverage. CI runs the full suite where project policy
requires it; don't starve a shared host. Use its existing interpreter and tooling,
not an incidental dependency install. Tests alone do not replace runtime proof.

Pick 2–4 load-bearing decisions appropriate to the risk: bounds, fail-closed paths,
ordering, identity checks. Break each in memory or an isolated review copy and name
the consumer assertion/regression that fails. Restore mutations; never leave them
in source. Report untested branches honestly rather than calling a narrow mutation
suite exhaustive. A passing happy path proves no refusal path.

Drive legitimate near misses and boundary cases as well as malicious/broken
inputs. Over-refusal can harm the customer as much as under-refusal. Use realistic
payloads, neighboring dates/bounds, units, spellings and missing/error markers.
Permanent regressions should catch plausible consumer-visible bugs, not wiring,
source text, mock echoes or incidental defaults. An independent reviewer returns
fixes to the implementer unless explicitly authorized to change source.

## 5. Historical population and reuse

Where a historical population exists, establish the whole applicable population
and execute changed behavior over it, within authorized resource bounds. Report
selection/exclusions, same/changed/refused/error distribution and wrong changes.
Compare BOTH versions per input for no-regression/equivalence claims. A spot check,
empty replay or count of identical exceptions is not a sound sweep.

Do not rerun a completed author sweep merely to repeat evidence. Read its executable
method, revisions, manifest/results and selection criteria, and determine whether
it supports the exact claim. Reuse sound proof with attribution; rerun only when
missing/disputed and authorized. Missing or unaffordable mandatory proof remains
NOT_REVIEWED. Do not spend beyond the task's authority to manufacture a verdict.

## 6. Red flags: every applicable review

Report each class as a located finding, none found in inspected scope, or unchecked:

| Class | Check |
|---|---|
| Dead code | Live callers/arguments/configuration; meaningful mutation or actual consumer reachability. |
| Code deciding what data means | **Blocks.** The model judges meaning; code validates typed values (owner, 2026-10-07). Any code that decides a value's meaning, unit, role or category from its appearance blocks: a regex or substring test over prose, a human-edited document or a string the system wrote itself; a vocabulary/alias list deciding what a column is; a magnitude threshold deciding a unit; a name-based guess. This is a design defect, not a heuristic to harden: name the typed declaration that should carry it (written by a validating CLI after the model reads the source or asks the user) and the fail-closed default when it is absent. Never ask for more cases or vocabulary: each round fits the examples in view. Pure normalization of a value the declaration already fixes (whitespace, case for equality) is not a judgment. |
| Events recognized from text the system printed | **Blocks.** Deciding what happened by reading output the system produced: parsing shell commands to tell which program ran, recognizing a result or refusal by a banner or heading, combining an exit code with printed text, finding a version or table by a file-name pattern, regex over formula text. The component that knows the fact must emit it as data at the source (a JSON record it writes, a typed field, a return value), and the consumer reads that. Ask: "who already knows this fact for certain, and why isn't it handed over as data?" (owner 2026-10-07, chat-eval trace reader). |
| Consumes what nothing produces | **Blocks a PASS that claims the feature works.** A component reads an artifact (a file, record, field) that no merged or reviewed code writes, so the feature cannot run end to end (e.g. a grader reading per-turn claims no extractor writes). State plainly what is missing and that the PR is a part, not the feature; never let tests that hand-write the missing artifact stand in for the producer. |
| Structure dropped, then re-parsed | **Blocks.** A stage holds a fact as typed data (a field, a dict, a dataframe column, a return value) but passes on only a rendered form (a string, a number format, a sheet label, Markdown, SVG, a log line, a file name), and a later stage parses that rendering back to recover the fact (owner, 2026-10-08: "happens all the time"). Typical shapes: a period or unit recovered from a display format that was rendered from a known value; numbers read back out of a rendered chart or document; a source or reference regexed out of prose that an earlier step had as a field; a refusal known only as a sentence, not a reason id. The fix is at the producer: keep the typed value and carry it forward (a runtime field, a run-record field, a manifest entry) so the consumer reads it directly; delete the parser. Ask of every changed producer: what typed facts does it have that it does not pass on, and who needs them later? Ask of every changed consumer: is it recovering something an earlier stage knew? |
| Built in-house where a maintained library fits | Note, not a block. Before reviewing generic infrastructure (an eval harness, a parser, a scheduler, a retry layer, a diff), ask whether a maintained library already does it and name the candidate and what would not fit. The owner decides; the review records the question. |
| Unjustified quantitative bounds | Measured rationale, population near the boundary, identity versus mere plausibility. |
| Overengineering | Callers, existing owner, machinery cost and what a simpler shape would lose. |
| Module proliferation | How many files must change one decision; coherent module concerns versus split-for-size. |
| Unneeded backward compatibility | On a greenfield project (no released product, or no consumer outside the team), any shim, alias, fallback, legacy reader, dual path or migration kept for the project's own earlier formats, templates, commands or records. It is allowed only with a proven, stated customer need: who depends on the old form, and why they can't move. Otherwise the old path is deleted in the same change and every caller migrated; spend no coding or review time keeping it alive. |

Flags block only at the stated blocking bar. Keep the concrete simpler-shape and
architecture-fit answers beside the net-value call; don't hide findings in prose.

## 7. Final verdict and exact-head gate

Report exact head/base, implementer and reviewer identities, executed commands and
results, runtime readback, mutations/probes, historical method, ranked findings
with repros, notes, omissions and net value. One row per requested PR; an unrun
check is unrun. Distinguish executed evidence from inspected/reused evidence.

- **PASS:** independent review completed all required acceptance/QA at this head.
- **BLOCKED:** observed actionable defect or hard gate, with owner and evidence.
- **NOT_REVIEWED:** incomplete required proof/bounded run, without pretending that
  passing tests or a green process exit supply a final verdict.

Verify required CI on the exact reviewed head, live PR head, conflicts and real
stack dependencies. SKIPPED is not success. Do not resolve/push even mechanical
conflicts as part of a read-only review; hand them to the authorized implementer.
A publication/doc integration needs the applicable scoped final-head check;
runtime changes/conflicts need substantive re-review. Preserve sound completed
checks, don't automatically repeat the whole review or suppress CI failures.

Publish a PR comment with identity and exact head when authorized; otherwise save
an identified local report that tracker can verify. PASS is not owner approval,
merge permission or deploy authority. If blockers survive the allowed rounds,
return the complete violated invariant plus executable repros, not piecemeal fixes.
