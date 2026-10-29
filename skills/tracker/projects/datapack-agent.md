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
- The owner-dash matrix is the approval queue (owner 2026-10-29: "this is the new system"). Repost it via pr-matrix whenever a PR becomes ready for approval, is approved, merges, or goes on hold, in the same turn as the chat update. Every PR with a posted walkthrough sits in a table row (the dash attaches walkthroughs to table rows only), even while it finishes checks, with its status in the row (owner 2026-10-29: "368 is not on the dash yet").
- Every walkthrough links only the PR's changes page: [#N](https://github.com/American-Securities/datapack-agent/pull/N/changes) (owner 2026-10-20).
- Markdown edits (FEATURE_MAP, SKILL, references, docs) change only the words that must change, in place: never rewrite, reword or reflow a line or paragraph wholesale, so the owner's diff shows just the change (owner 2026-10-22). Every brief carries this rule.
- Every walkthrough's Code section includes a "data journey": how the data flows from source to output through the changed code, before → after, illustrated with the actual code lines that move it (owner 2026-10-22).
- Agent context is the orchestrator's job (owner 2026-10-23). Never send a review round or follow-up to a worker whose context is large (> ~150K tokens); long contexts bill as extra usage, not the weekly plan, and on 10-22 they exhausted the monthly spend cap and stalled every lane. Start a fresh agent instead, with a short handoff: worktree path, branch, head, what's done, what's left, the review file. Reviewers likewise: a fresh reviewer per round when the previous one is long.
- Mechanical work goes to `dpa-mech` (Sonnet 5.5) in a tightly scoped brief (owner 2026-10-23): git syncs and merges from main, merge-on-green, golden rebakes to a stated spec, conflict resolution with a stated rule, worktree cleanup, CI reruns, PR body edits. Opus (`dpa-impl`) only for design, code changes that need judgment, and the walkthroughs.

## Speed rules (2026-10-07)

- Self-review against review-pr's red flags before review; the result file lists each
  check.
- Focused tests locally (Linux venv; Windows venv
  `/mnt/c/CodeProjects/_dpa_scratch/venv312/Scripts/python.exe` for Excel/COM); CI is
  the full-suite gate.
- A reviewer's non-blocking notes become follow-ups, not another round.
- Keep the next briefs approved ahead.

## Dev checks every implementer result carries

- No new code comments (owner, DESIGN_agentic_practices §7/§12): every PR must NET REMOVE comments (owner 2026-10-26): `#` comment lines plus docstring lines, added minus removed, must be < 0 per PR, not merely ≤ 0. A PR at exactly 0 is a defect: delete stale or non-constraint comments in the touched files until the count is negative. Only constraint comments survive. Every brief says so; every result reports the count; every walkthrough states it plainly (e.g. "comments −3"), never "net zero".
  Owner (2026-10-20): a comment or docstring the change touches or makes stale is DELETED, never rewritten into a "correct version". Pre-checks flag any rewritten comment as a defect.

- Reuse inventory BEFORE writing each new function, constant, list or writer (CLAUDE.md,
  CONTRIBUTING "Reuse first"): `python dev/tools/inventory.py --grep <word>` over the
  WHOLE plugin, once per capability word (what it reads or does: `parquet`, `cleaned`,
  `sheet`, `cagr`), never `--module` alone. The PR's Reuse section lists, per new
  symbol, the commands run and the hits (path:line) with reuse / distinct-because.
  A Reuse line citing only `--module <the file I edited>` is NOT_REVIEWED. (#214's B1
  fix ran `--module excel_build` and missed `data_reader.read_cleaned_parquet`, which
  `--grep parquet` lists.) Reviewers and the orchestrator re-run the greps.
- Size: `python dev/check_pr_size.py <base> <head> --pr N` (800-line guide; owner
  exceptions are recorded per PR).
- Focused tests and their output; a result without them is NOT_REVIEWED.

## Paths

- Lanes, results and reviews: `/mnt/c/CodeProjects/_dpa_scratch/owner_review/lanes/<lane>/`.
- Briefs and worker sessions: `~/dpa-lanes/` (not `/tmp`, which a reboot clears).
