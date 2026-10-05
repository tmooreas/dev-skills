# dev-skills

Portable meta skills for evidence-first development across coding-agent runtimes.
This repository is the source of truth; project instructions supply product rules,
architecture contracts, priorities and production permissions.

| Skill | Purpose |
|---|---|
| [tracker](skills/tracker/SKILL.md) | Named ownership, bounded async orchestration, compact context, independent review, dependency/CI gates and authorized mechanical execution. |
| [review-pr](skills/review-pr/SKILL.md) | Seven review questions, helper ownership, actual consumer proof, mutations/near misses, historical comparisons and exact-head verdicts. |
| [issue-queue](skills/issue-queue/SKILL.md) | Current-main backlog reconciliation, next-action sequencing and executed proof before issue closure. |

## Use

Clone once and link the skill directories, rather than copy their instructions.
Read only the invoked skill and necessary supporting references. The tracker
[runtime adapters](skills/tracker/runtime-adapters.md) are separate from its core
policy so CLI-specific details do not enter every task's context.

```sh
git clone https://github.com/tmooreas/dev-skills.git
cd dev-skills
mkdir -p "$HOME/.agents/skills"
for skill in tracker review-pr issue-queue; do
  target="$HOME/.agents/skills/$skill"
  if [ -e "$target" ] || [ -L "$target" ]; then
    printf 'Already exists; reconcile before linking: %s\n' "$target" >&2
    exit 1
  fi
  ln -s "$PWD/skills/$skill" "$target"
done
```

`.agents/skills` is omp's native provider-neutral discovery path. For Claude,
use `$HOME/.claude/skills` instead. Do not run both recipes blindly: review any
existing installation before moving/replacing it. Runtime skill roots and command
spelling can differ; omp uses `/skill:tracker`, `/skill:review-pr` and
`/skill:issue-queue`, while Claude uses `/tracker`, `/review-pr` and `/issue-queue`.
If automatic discovery is disabled, explicitly read the desired `SKILL.md`.
Keep the three skills together so their sibling references resolve.

Owner approval, reviewer PASS, current-head CI, merge and deployment remain
separate permissions. Invoking a skill grants none of them implicitly. Cheaper
mechanical execution is bounded to a verified allowlist, not conflict resolution
or correctness review. No service, orchestrator launcher or model dependency is
introduced by this repository.

## Tracker collector and local verification

The bundled stdlib Python collector is read-only toward GitHub and watched source:

```sh
python3 skills/tracker/scripts/tracker.py --help
python3 skills/tracker/scripts/tracker.py --checkout /path/to/watched/repo pr 123
python3 skills/tracker/tests/test_tracker.py
python3 skills/tracker/tests/mutants.py
```

The collector's session/signature adapters and local storage are still
Claude-specific; it does not discover omp jobs or implement all orchestration
states. The policy is runtime-agnostic, not a claim that this legacy collector
was rewritten. Its behavioral tests use temporary git fixtures and need no
EarningsPulse checkout, GitHub credentials or model calls. The mutation harness
removes its own temporary generated source.

Verify skill edits with the installed runtime's loader/prompt builder and CLI
help without paying for a model call. A loading skill is not automated enforcement
of its prose rules; runtime permission controls, project CI and merge hooks remain
separate. Respect project resource limits and explicit cost/telemetry deferrals.

## Origins and maintenance

Initial publication, 2026-10-05:
- Tracker: the canonical Claude-side skill plus this session's provider-neutral
  CLI orchestration, context discipline, independent delta audits and mechanical
  executor policy.
- Review-pr: EarningsPulse's latest helper-to-owner review drill, including the
  changes independently reviewed in PR #1371.
- Issue-queue: EarningsPulse's current-main triage and strong closure-proof rules.

The shared versions retain those methods but resolve repository-specific commands,
paths, issue IDs, numeric limits and production policies from the target project.
They do not export tracker state, transcripts, credentials or cached dependencies.
Existing project installations are not overwritten by publication. Update this
repository first and deliberately reconcile any local specialization; do not
maintain another independent orchestration policy in a launch prompt.
