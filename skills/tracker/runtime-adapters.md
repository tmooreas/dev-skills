# Tracker runtime adapters

The policy in [SKILL.md](SKILL.md) is authoritative. These are replaceable
transport/CLI recipes, not a provider requirement, merge grant or safety sandbox.
Read only the adapter needed for the task; do not inject this whole file into
every worker prompt. CLI examples use placeholders that must be filled from the
verified task contract, installed `--help` and working authentication.

## One skill, discoverable across runtimes

The source of truth is `skills/tracker/` in `tmooreas/dev-skills`. Install or
link that directory into a runtime's skill root rather than maintaining policy
copies. Native, provider-neutral discovery uses `~/.agents/skills/tracker`;
Claude uses `~/.claude/skills/tracker`. omp supports `.agents/skills` by default;
foreign user `~/.claude/skills` discovery is opt-in. Do not change provider
settings just to discover it. If discovery is disabled, read the installed
skill explicitly. In omp the invocation is `/skill:tracker`; another runtime
may expose `/tracker`. Do not promise identical command spelling everywhere.

## Existing fact collector (legacy Claude storage)

Run from the watched repository with the existing Python interpreter; substitute
the actual installed skill directory for `<tracker-skill>`:

```sh
python3 <tracker-skill>/scripts/tracker.py --help
python3 <tracker-skill>/scripts/tracker.py sweep
python3 <tracker-skill>/scripts/tracker.py pr N --comments all
python3 <tracker-skill>/scripts/tracker.py sessions --hours 6 --turns 2
python3 <tracker-skill>/scripts/tracker.py diff
```

Global flags `--repo owner/name`, `--checkout PATH`, `--json` precede the
subcommand. `record N --comment ID --kind review --verdict PASS|BLOCKED|UNCLEAR
--round R --head SHA --summary '...'` records a comment only after it is read.
Non-review kinds: note, decision, fixes, other. Exit codes: 0 (inspect per-item
errors), 2 could not run, 3 record refused.

It fetches/read-inspects git and uses GET-only GitHub requests; snapshots and
comment ledger are under `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tracker`.
Its session scanning, signatures and verdict enum are Claude-specific. It does
**not** discover omp jobs, store local-file verdicts or implement NOT_REVIEWED and
USER_DEFERRED states. Supplement those with the task's explicit local ledger and
actual job results; do not claim the script was migrated. Keep current ownership
from signed PR/commit evidence; infer no omp identity from a Claude-only scan.
Use `nice -n 19 ionice -c3` on the shared production host. A sweep can run async;
its collector behavior does not authorize mutation of the watched repository.

## omp / GPT or another configured model

Example bounded read-only review launch, with an explicit task packet:

```sh
timeout <outer-seconds> omp -p --model <review-model> --thinking low \
  --max-time <duration> --cwd <isolated-worktree> --session-dir <job-session-dir> \
  --mode text --no-title --no-pty --no-extensions --no-skills --no-rules \
  --tools read,bash,grep,glob,lsp,write @<task-packet>
```

The packet must name this canonical policy and the project's review drill and
mandatory safety contracts, because automatic rules/skills were disabled.
`write` is permitted only for the named report artifact, not source edits. A bash
allowlist in prose is **not** a filesystem/network sandbox; use existing wrappers
or approval controls when the environment requires enforcement. Read-only git
`diff/show/log/rev-parse/merge-base` and GitHub GETs remain allowed. Distinguish an
authorized fetch (updates remote refs) from source/index writes; never abbreviate
this to "no git". Do not pass `--no-lsp` merely to avoid required code intelligence.

Use `--resume=<saved-session-path>` only for a scoped authorized delta check, not
automatic recovery of an exhausted full review. Preserve session IDs and reports.
Model names are configurable; `openai-codex/gpt-6.1-sol` was the review model in
the 2026-10-05 session, not a permanent provider constraint. Example flags were
checked against installed omp v18.4.11; recheck after upgrades.

## Claude CLI / cheaper mechanical executor

Example bounded launch using the existing authenticated normal mode:

```sh
timeout <outer-seconds> claude -p --model <role-model> --effort low \
  --max-budget-usd <request-stop-threshold> --output-format json \
  --permission-mode <permitted-mode> --tools Read,Bash -- '<scoped packet>'
```

`--` before the prompt terminates variadic `--tools`. Choose a model actually
available to the authenticated CLI; Sonnet 5.5 is the owner's suggested cheaper
mechanical role, not an independent review requirement. Opus 5.5 was used for
implementation/auditing in the session. Use only the tools/actions needed.
For implementer jobs add Edit/Write explicitly and use an isolated worktree.
No tools for dispatching nested agents or other model calls.

On this host (2026-10-05), normal authentication worked and `--bare` failed
authentication. Isolated/bare mode is conditional on already-working supported
auth, never a reason to copy secrets, change credentials or bypass required hooks.
Automatic normal-mode context can be large; inspect reported token/cache usage.
Resume `total_cost_usd` is cumulative: record the latest total, not its sum with
prior totals. Budget exhaustion may overshoot one request; preserve partial
feedback but do not claim a clean completed audit or review.

For async execution, use the host's finite background-job facility and completion
notifications (omp tool `bash` with `async: true`, or the equivalent runtime).
Do not append an untracked shell `&`, repeatedly poll, or spawn a no-op job.
The mechanical packet is a short exact allowlist containing:
- repository and isolated checkout; one ordered list of PRs with exact heads;
- independently verified PASS source/head, green required CI, satisfied bases;
- actual owner authorization source/entry and scope, not a fabricated quote;
- the project's permitted merge command/method and required hooks/signature;
- recheck immediately before each merge, one merge per tool call, stop on drift,
  conflict, failed/missing gate or unverifiable authority; no auto-merge;
- no code edits, conflict decisions, force push, stash/reset, cleanup or deploy;
- final per-PR state and merge SHA, or exact hold evidence; saved output location.

The owner can delegate mechanical execution to this bounded role; otherwise the
author merges. The orchestrator decides the eligible allowlist before launch.
The executor cannot turn NOT_REVIEWED into PASS or repair a blocked PR by choosing
its own conflict resolution. A rejected hook is a stop, not permission to bypass.
No separate mechanical-git skill was found in the inspected global, synced,
plugin or EarningsPulse Claude skills on 2026-10-05; this section supplies the
requested Claude-side recipe without inventing an installed helper.

## Message-capable runtimes

If a live messaging API exists (for example `SendMessage`/`ListAgents`), discover
current addresses rather than using an old address book. Match stable session ID,
title and live process; ambiguous recipients receive no mutation instructions.
Refresh addresses after restart. Read both chat text and message payloads for
verdicts. Deliver per-recipient packets with exact authority and stop conditions;
record whether delivery was accepted, held or refused. A saved artifact or PR
comment is evidence of a handoff, not a running fix. CLI runtimes need no message
API: use the scoped job or explicit continuation described above.

## Verification and enforcement limits

Validate edits through the installed skill loader and prompt builder, plus CLI
help, without launching a model. Test native discovery and canonical realpath
identity, not merely frontmatter text. The legacy collector can be smoked with a
read-only PR lookup. This skill adds no service, launcher, automated merge gate,
CI enforcement or model-context cap. Permissions, job limits and evidence gates
remain orchestration discipline unless existing runtime controls enforce them.
