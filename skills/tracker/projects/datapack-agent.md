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
- Models, as agents in `~/.omp/agent/agents/` (the skill's "Route by kind of work"):
  `dpa-mech` is Sonnet 5.5 (owner 2026-10-23), `dpa-impl` is Opus 5.5, `dpa-review` is
  `openai-codex/gpt-6.1-sol` (owner 2026-11-02). `openai-codex/gpt-6-astra` is an advisor only: a bounded, read-only
  question on a hard design or a disputed finding, with a compact packet; never routine
  review or implementation (expensive). Claude models only when `omp usage` shows
  allowance left; warn the owner about Claude usage before launching one.
- Commit identity: author and committer "Tom Moore" <tmoore@american-securities.com>.

## Memory (WSL out-of-memory crash, 2026-10-08)

/tmp is a 7.7 GB RAM disk on this machine, shared with the esr project's agents. Scratch worktrees, venvs, temp deal copies and pytest's basetemp go under `~/scratch` (launch workers with `TMPDIR=~/scratch/tmp`), never /tmp. pytest runs with `-n 4` at most, never `-n auto`. Load one large workbook at a time. Smokes copy deals to `~/scratch`, not `/tmp`.

Agents share this machine with the orchestrator's local test suites. To stop a process, kill only the PIDs you started; never pattern kills (`pkill -f pytest`, `killall python`). On 2026-10-29 one such kill stopped two merge-gate suites mid-run.

Dispatch by measured headroom, not a fixed agent count (owner 2026-11-05). Before each spawn, run `owner-dash --project datapack-agent approvals` and confirm the lane's brief is approved (new work and bug fixes alike; only reviews and mechanical git work on approved work are exempt; owner 2026-11-06), then read `free -g` (available) and weigh the job: read-only, review-of-text and mechanical git lanes are light (under 1 GB); a lane that builds, ingests or recalculates a real deal is heavy (several GB, through `~/scratch/bigjob`, one slot per lane); Excel is one at a time behind the lock. Start light lanes while available memory stays above about 4 GB; start a heavy lane only when its expected size fits above that margin.

Approval carries over (owner 2026-11-05) when the only change since the approved head is a rebase onto main and/or removed lines (a scope trim): prove it by machine (the range-diff or patch diff shows no added or changed lines beyond conflict-free rebase context), merge through the normal gate, and send the owner a one-line note. Any added or changed line needs approval again.

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
- Every walkthrough links only the PR's changes page: [#N](https://github.com/American-Securities/datapack-agent/pull/N/changes) (owner 2026-10-20).
- Markdown edits (FEATURE_MAP, SKILL, references, docs) change only the words that must change, in place: never rewrite, reword or reflow a line or paragraph wholesale, so the owner's diff shows just the change (owner 2026-10-22). Every brief carries this rule.
- Every walkthrough's Code section includes a "data journey": how the data flows from source to output through the changed code, before → after, illustrated with the actual code lines that move it (owner 2026-10-22).
- Agent context is the orchestrator's job (owner 2026-10-23). Never send a review round or follow-up to a worker whose context is large (> ~150K tokens); long contexts bill as extra usage, not the weekly plan, and on 10-22 they exhausted the monthly spend cap and stalled every lane. Start a fresh agent instead, with a short handoff: worktree path, branch, head, what's done, what's left, the review file. Reviewers likewise: a fresh reviewer per round when the previous one is long.
- Every brief goes on the owner dashboard (owner 2026-11-02): `owner-dash --project datapack-agent put brief <name> <file>` when it is first shown to the owner, and again on every change. Its name matches the file in `~/dpa-lanes/briefs/`. The first line under the title states its status: awaiting approval, approved (date), or done (PR links).
- The owner approves PRs and briefs with the dashboard's Approve button (owner 2026-11-02); a chat approval still counts, and you record it at once with `owner-dash --project datapack-agent record approve pr|brief <N|name>` so the page agrees. Before every merge or launch, run `owner-dash --project datapack-agent approvals`. A PR approval counts only at the head it names (`current`). A head that moved afterwards needs a new click or the restack rule above, stated in chat. Ledger every approval you act on.
- A brief approval never approves a PR (owner 2026-11-06, on retro briefs for PRs started without one): it approves the scope; the PR still needs its own walkthrough and approval at its head.
- Watch for dashboard approvals without being asked (owner 2026-11-02): keep a background job running that exits when `approvals.jsonl` gains a row and prints the new rows, and start it again after acting on each one. Act on a PR approval at `current` straight away (gate, merge, repost), and on a brief approval by launching it.
- Eval and comparison results go on the dashboard's Reports page (owner 2026-11-02, "pull up the compare eval output"): `owner-dash --project datapack-agent put report <name> <file>` when the run finishes, named `<eval>-<deal>` (e.g. `compare-rts`, `titan-heldout`). Post the scorecard the owner reads, not the working notes; repost on every rerun.
- Review findings are fixed only when they will show up in real deals (owner 2026-11-02, on the GPT backfill of #310-#372: "some of those serious bugs are quite outlandish"). Before a finding becomes a fix, test its trigger on real deal data and ordinary associate workflows and class it SEEN, LIKELY or CONTRIVED; CONTRIVED findings go on the backlog, not into a PR. Never call a finding serious from a hand-built repro alone.
- The workbook compare is the launch gate and scores top-down (owner 2026-11-02, with Matt and Jono): headline numbers first (revenue, gross profit, gross margin %, EBITDA where present, customer count, top-10 customers), then sub-headlines, then detail, drilling down only to explain a gap. Numbers within 1% pass; customer lists score by overlap; a definition difference is recomputed under their definition before it counts as one. Packs built with DPA (Jacuzzi) are not scored.
- A held-out deal stays held out only until its first cold scored run is recorded (owner 2026-11-04: "things only have to be held out before theyve been run once cold after that theres no pt"). Then its folder is open to every lane, for triage, fixes and compares, and its results count as ordinary. Cases of the deal not yet run cold stay held out until they are.

## Speed rules (2026-10-07)

- Self-review against review-pr's red flags before review; the result file lists each
  check.
- Focused tests locally (Linux venv; Windows venv
  `/mnt/c/CodeProjects/_dpa_scratch/venv312/Scripts/python.exe` for Excel/COM); CI is
  the full-suite gate.
- A reviewer's non-blocking notes become follow-ups, not another round.
- Reviews go to `dpa-review` (GPT) again (owner 2026-11-02, "we should have gpt back for reviews now"), on `openai-codex/gpt-6.1-sol` (owner 2026-11-02, "6.1 sol"): a fresh session each round, independent of the implementer. Claude `reviewer` only when dpa-review is unavailable, and say so in the ledger.
- Keep the next briefs approved ahead.

## Dev checks every implementer result carries

- No new code comments (owner, DESIGN_agentic_practices §7/§12): every PR must NET REMOVE comments (owner 2026-10-26): `#` comment lines plus docstring lines, added minus removed, must be < 0 per PR, not merely ≤ 0. A PR at exactly 0 is a defect: delete stale or non-constraint comments in the touched files until the count is negative. Only constraint comments survive. Every brief says so; every result reports the count; every walkthrough states it plainly (e.g. "comments −3"), never "net zero".
  Owner (2026-10-20): a comment or docstring the change touches or makes stale is DELETED, never rewritten into a "correct version". Pre-checks flag any rewritten comment as a defect.
  Owner (2026-11-05): a PR that changes no code file (docs, SKILL/reference text, CONTRIBUTING only) is exempt; it reports "comments 0 (no code)".

- Reuse inventory BEFORE writing each new function, constant, list or writer (CLAUDE.md,
  CONTRIBUTING "Reuse first"): `python dev/tools/inventory.py --grep <word>` over the
  WHOLE plugin, once per capability word (what it reads or does: `parquet`, `cleaned`,
  `sheet`, `cagr`), never `--module` alone. Also search (`git grep`) the data each
  capability reads or writes: field, column, setting and table names (e.g.
  `full_years`), since a copy under another name reads the same data (owner
  2026-11-05: "latest full year, else latest" is inline in three places that only
  `git grep full_years` finds). The PR's Reuse
  section lists, per new symbol, the commands run and the hits (path:line) with
  reuse / distinct-because. A Reuse line citing only `--module <the file I edited>`
  is NOT_REVIEWED. (#214's B1 fix ran `--module excel_build` and missed
  `data_reader.read_cleaned_parquet`, which `--grep parquet` lists.) Reviewers run
  their own searches with words other than the author's, not the author's list again.
- Size: `python dev/check_pr_size.py <base> <head> --pr N` (400-line limit since owner
  2026-11-03; PRs open then keep 800; other owner exceptions are recorded per PR).
- Scope (owner 2026-11-05): one PR solves one issue or implements at most one feature.
  Briefs name that one issue or feature; anything else found on the way is an issue,
  not more commits. A bug already on main is fixed in this PR only if the PR causes or
  worsens it. Review fix rounds that outgrow the size limit or the one issue end in a split.
  A review finding that is a different defect from the PR's issue is its own PR (stacked if
  it needs this one), even when this PR exposed it (owner 2026-11-07, on #393: `_row`
  pairing and the CP1 reject hole should have been two PRs).
- Focused tests and their output; a result without them is NOT_REVIEWED.

## Paths

- Lanes, results and reviews: `/mnt/c/CodeProjects/_dpa_scratch/owner_review/lanes/<lane>/`.
- Briefs and worker sessions: `~/dpa-lanes/` (not `/tmp`, which a reboot clears).

- Excel automation crashes leave ~1.4 GB full dumps in C:\Users\tmoore\AppData\Local\CrashDumps (policy keeps 10). After any Excel run, delete EXCEL.EXE.*.dmp there.
