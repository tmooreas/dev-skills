# tracker adapter: datapack-agent (DPA)

The repository's CLAUDE.md, CONTRIBUTING.md and `dev/DEFINITION_OF_DONE.md` win over
this file. These rules were set by the owner on this project; promote one to the
generic skill only with the owner's say-so.

## Bounds and models

- Up to **three** simultaneous model jobs (owner 2026-10-07; overrides the generic two).
- Implementers: `anthropic/claude-opus-5-5`. Independent reviewers:
  `openai-codex/gpt-6.1-sol`. Mechanical executor (clean rebases/restacks, pushes of
  approved heads, retargets, CI reruns, closures already decided, owner-approved
  merges): `anthropic/claude-sonnet-5-5`; it hands back to an Opus implementer the
  moment a conflict touches logic or tests, or a test fails after a rebase.
- Commit identity: author and committer "Tom Moore" <tmoore@american-securities.com>.

## Launch recipe

Workers run in tmux inside WSL (owner 2026-10-08), session `dpa`, one window per lane:

```sh
~/dev-skills/bin/skills-sync
tmux new-session -d -s dpa 2>/dev/null
tmux new-window -d -t dpa -n <lane> "cd <cwd> && omp -p --model <model> --thinking medium \
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
  on a copy of a deal in a temp folder with `~/dpa/venv312`, never in `Deals/`.
- Merges need the owner naming the PR; the executor rechecks head, base, CI and
  mergeability immediately before merging.
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
