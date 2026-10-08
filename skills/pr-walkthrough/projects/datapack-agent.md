# pr-walkthrough adapter: datapack-agent (DPA)

The repository's CLAUDE.md, CONTRIBUTING.md and `dev/DEFINITION_OF_DONE.md` are binding
and win over this file. DPA turns a deal's data (revenue cube, customer master, opex,
suppliers, pipeline, census) into an Excel datapack with live formulas, a draft deck and
chat answers. Excel on native Windows is the numeric authority for formulas; the
formula engine (`extract/formula_engine`) is the fast reader.

## Safety

From WSL, Excel/COM tests and `powershell.exe` run on the owner's real desktop. A
walkthrough starts no Excel and no Windows process; it reads existing Excel test output
(review files, CI) instead. Smokes run on Linux (`~/dpa/venv312`) through the real
runners on a copy of a deal in a temp folder, never in `Deals/` and never on a real
deal's folder. No GitHub writes.

## Prepare

- Status: `gh pr view N --repo American-Securities/datapack-agent --json headRefOid,baseRefName,isDraft,state,additions,deletions,statusCheckRollup,body`.
  Check names: `size`, `test`, `shard (pipelines|determinism|rest)`. A cancelled run
  next to a successful one of the same name is a superseded duplicate.
- Diff: `git diff <base>...<head>` in a scratch worktree (`git worktree add --detach
  ~/walk-N-<sha7> <sha>`, removed at the end). Plugin code is under
  `plugin/skills/datapack-agent/scripts/`; cut families under `cuts/<family>/`
  (`definition.py` facts, `calculator.py` data, `layout.py` the tab).
- Reviews: `/mnt/c/CodeProjects/_dpa_scratch/owner_review/lanes/<lane>/` (review and
  result files, dated sections; match the head SHA).
- Size: `python dev/check_pr_size.py <base> <head> --pr N` (800-line guide; note any
  owner exception). Reuse: `python dev/tools/inventory.py --grep <name>`.
- Read typed records, never printed banners: run records `<deal>/Logs/runs/<id>.json`
  (`exit_code`, `refused`, `outputs`, `result`), `result.qf_results`,
  `result.failed_builds[i].facts`, carry-forward records.

## Output changes

- Section 2 names the incident by deal, tab, row and number (e.g. "retention Total 110
  vs cube 135"), and the owner decision with its date.
- Section 3's diagram runs from the deal's data through ingestion -> CP1 sign-off ->
  ideation (CUT_PLAN) -> build (pretied `_src_*` sheets -> tab) -> verify/QF -> what the
  associate sees.
- Section 5's worked example is a real build: copy `Deals/NovaTech_Synthetic` (and the
  RTS-shape fixture, `tests/make_rts_shape_fixture.py`, when periods or fiscal years
  matter) to a temp folder, run `run_ingestion.py`, ideation with
  `cleaning_signoff_override`, `run_excel_build.py --override-reason`, then render the
  tab with `extract.formula_engine.render_tab`. Quote the rendered rows, the QF line and
  the run record. Show the edge case by editing the copy's raw CSV (drop months, blank
  a column) and say what you changed.
- Section 7 adds: Excel-on-Windows test results for this head (CI does not run COM),
  the golden-workbook delta (which tabs and cells), and the oracle
  (`dev/tools/deal_oracle.py`) where numbers move.
- Section 8's independent answers here are: the revenue cube itself (a tab's Total
  must equal the cube's revenue), the financial-summary sheet built by its own
  group-by, the oracle, and values worked by hand. A golden rebaked by the PR, or an
  expectation read off the PR's own output, is not independent.
- Section 11 adds the stack (the PR this one is based on, which PRs are stacked on it)
  and whether the base is main.
- Add section **9a. Formatting conventions** after the antipattern check, one line
  each: zero shows as an en-dash "–", never blank; an undefined ratio shows "NA"
  (`YOY_NA`); `$` only on a tab's first data row; partial years labelled "(NM)"; every
  analysis tab has a Check row that evaluates to zero against an independent sheet.
- Under "Judgments in code", owners are the owner (dated decision), a recorded design
  note under `dev/`, the deal's declarations (`Docs/deal.yml`, `schema_domains.yml`,
  the signed cleaning report), or nobody (a finding).

## Extra antipatterns

- **Proof on clean synthetic data only:** NovaTech has calendar years, monthly rows,
  January customer starts, no missing values. A change touching periods, fiscal years,
  partial years, missing gross profit, customer ids or units needs the RTS-shape
  fixture or an edited copy, not NovaTech alone.
- **Full years or periods read from a format:** anything deciding full/partial years,
  fiscal years or units from a number-format string or header text (B1).
- **Lost money:** a tab whose Total does not equal the cube's revenue for the same
  period, or whose Check cells are blank, or that wraps cohort/data cells in
  `IFERROR(...,0)`.
- **Unknown becomes zero:** a missing gross profit, cost or revenue summed as 0
  (`fillna(0)`, pandas `sum` over NaN) instead of staying unknown and refusing.
- **Deal folder writes:** code or tests writing into `Deals/` (only the two listed
  synthetic deals are committed, with the owner's per-change exception).
- **Refusals without a record:** a deliberate stop that exits non-zero without
  `refuse(reason)` / a typed refusal in the run record.
- **Plan files rewritten by code:** a saved `Docs/CUT_PLAN.md` or other associate-approved
  file changed by the pipeline instead of flagged.
