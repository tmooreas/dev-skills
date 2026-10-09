# tracker adapter: datapack-agent (DPA)

The repository's CLAUDE.md, CONTRIBUTING.md and `dev/DEFINITION_OF_DONE.md` win over
this file. These rules were set by the owner on this project; promote one to the
generic skill only with the owner's say-so.

## Bounds and models

- **One model job at a time** (owner 2026-10-09: "further optimize usage and minimize
  threads"; overrides the earlier three). Queue the next job behind the running one.
  The orchestrator does small mechanical work (merges after gates, pushes, short
  checks) itself instead of launching a worker. Reviews after a fix are scoped deltas,
  never a full re-review. Resume an existing session rather than start a fresh one
  when the context is still relevant.
- **Ledger plus aggressive compaction** (owner 2026-10-09): `~/dpa-lanes/LEDGER.md` is the
  durable source of truth for decisions, PR heads/states, branches, queue and next steps.
  Update it at every event (launch, verdict, head change, merge, owner decision); compact
  the orchestrator's context aggressively instead of restarting; after a compaction, re-read
  the ledger before acting (it wins over the summary). Jobs are queued in
  `~/dpa-lanes/queue.txt`, started one at a time by the `queue` tmux window.
- Models (owner 2026-10-09, after Claude usage ran out): `openai-codex/gpt-6.1-sol`
  implements, reviews (a separate session from the implementer) and executes
  mechanical git. `openai-codex/gpt-6-astra` is an advisor only: a bounded, read-only
  question on a hard design or a disputed finding, with a compact packet; never routine
  review or implementation (expensive). Claude models only when `omp usage` shows
  allowance left; warn the owner about Claude usage before launching one.
- Commit identity: author and committer "Tom Moore" <tmoore@american-securities.com>.

## Memory (WSL out-of-memory crash, 2026-10-08)

/tmp is a 7.7 GB RAM disk on this machine, shared with the esr project's agents. Scratch worktrees, venvs, temp deal copies and pytest's basetemp go under `~/scratch` (launch workers with `TMPDIR=~/scratch/tmp`), never /tmp. pytest runs with `-n 4` at most, never `-n auto`. Load one large workbook at a time. Smokes copy deals to `~/scratch`, not `/tmp`.

## Launch recipe

Workers run in tmux inside WSL (owner 2026-10-08), session `dpa`, one window per lane:

```sh
~/dev-skills/bin/skills-sync
tmux new-session -d -s dpa 2>/dev/null
tmux new-window -d -t dpa -n <lane> "cd <cwd> && TMPDIR=~/scratch/tmp omp -p --model <model> --thinking medium \
  --mode text --no-title --no-pty --skills tracker,review-pr \
  --tools read,bash,grep,glob,lsp,edit,write,wait --auto-approve --max-time <n>m \
  --session-dir <dir> @<brief> 2>&1 | tee <lane>.out"
```

The owner watches with `sudo -u ompreview tmux attach -t dpa`. A window closing means the
worker exited; its `.out` file and session hold the result.

## Owner gates

The generic skill's brief approval, mechanical-git exemption and smoke-before-owner
gates apply. Here:
- The smoke runs the real runners (`run_ingestion.py`, ideation, `run_excel_build.py`)
  on a copy of a deal under `~/scratch` with `~/dpa/venv312`, never in `Deals/` or /tmp.
- Merges need the owner naming the PR; the executor rechecks head, base, CI and
  mergeability immediately before merging.
- **Standing approval for run_cut slices** (owner 2026-10-08: "all these run cut ones are basically the same and i think we have the right scheme here so as long as there no scope creep i feel good about them"): a brief for a run_cut slice that follows the approved scheme (typed request validated against the cut's definition; keys as JSON objects of declared fields; layout emits key + cell kind; answers.py / run_cut.py reused; refusals with declared reasons; provisional before CP1) launches without asking. Anything beyond that scheme, or any scope creep, goes back to the owner. Merges still follow the walkthrough rule.
- Never ask the owner to approve a merge before a full pr-walkthrough of that PR
  (owner 2026-10-08). Once the owner has approved a PR, merge it when its gates pass;
  do not ask again (a later restack with no behaviour change keeps the approval).

## Speed rules (2026-10-07)

- Self-review against review-pr's red flags before review; the result file lists each
  check.
- Focused tests locally (Linux venv; Windows venv
  `/mnt/c/CodeProjects/_dpa_scratch/venv312/Scripts/python.exe` for Excel/COM); CI is
  the full-suite gate.
- A reviewer's non-blocking notes become follow-ups, not another round.
- Keep the next briefs approved ahead.

## Dev checks every implementer result carries

- Reuse inventory for each new helper or constant: `python dev/tools/inventory.py --grep <name>`.
- Size: `python dev/check_pr_size.py <base> <head> --pr N` (800-line guide; owner
  exceptions are recorded per PR).
- Focused tests and their output; a result without them is NOT_REVIEWED.

## Paths

- Lanes, results and reviews: `/mnt/c/CodeProjects/_dpa_scratch/owner_review/lanes/<lane>/`.
- Briefs and worker sessions: `~/dpa-lanes/` (not `/tmp`, which a reboot clears).
