# pr-walkthrough adapter: early-stage-returns (esr)

The repository's CLAUDE.md is binding and wins over this file. It builds an LBO returns
workbook (live Excel formulas) and a deck from `assumptions.yaml`. Excel-COM on native
Windows is the numeric authority.

## Safety

From WSL, `powershell.exe`, `cmd.exe` and `taskkill` run on the owner's real desktop.
A walkthrough starts no process except the read-only `esr-dev pr status` and GitHub
reads. It stops and signals nothing, and never touches the owner's Office apps (a test
mutation once killed their PowerPoint). Throwaway probes call Python functions
directly and never go through the Windows helper.

## Prepare

- Status: from `~/code/esr-tooling`, `uv run esr-dev pr status N`. Body, base and
  checks: `pr://N`. Diff: `pr://N/diff/all`, or `git diff <base>...<head>` in a scratch
  worktree (`git worktree add --detach /tmp/walk-N-<sha7> <sha>`, removed at the end).
- Exported symbols: LSP references, not text search.
- Reviews: `uv run esr-dev review show N` (out/reviews) and the PR comment. Native
  Excel evidence: `out/native/<id>/` and the patch record's `verification` section
  (checks n/n, scenarios n/n, returns as shipped).
- Read typed records (`ESR_RESULT` footers and their `outputs`, patch records, named
  cells), never SAY lines.

## Output changes

- Section 2 names the workbook incident by sheet!cell and number where there is one.
- Section 3's diagram runs from assumptions.yaml or the CLI through the fill, the
  workbook and QC to the user.
- Section 4 names, for template changes, the sheet!cell and its recipe in
  `esr/template_patch.yaml`.
- Section 5 quotes IRR/MOIC from Excel-COM only. Label engine or LibreOffice figures
  "indicative".
- Section 7 adds the native record (checks, scenarios, SHA), the oracle where returns
  move, and the executed review with what it ran.
- Section 8's independent answers here are, in order: Excel-COM's own calculation (a
  frozen native record), the oracle (`esr/oracle.py`), a golden model's shipped answer,
  and values worked by hand in the test. An expected IRR, cell value or formula copied
  from the patched template, the engine or a previous run is not independent. A test
  that pins the template's current wrong value is codifying a bug.
- Section 11 counts committed evidence (`tests/evidence/`, xlsx) separately from the
  800-line guide.
- Add section **9a. Standing questions** after the antipattern check, a sentence each:
  - Are all the defaults this PR's inputs need filled, with nothing left at a template
    sample value?
  - Is the model parametrized enough, with no assumption hardcoded and no deal fact
    lacking an input?
- Under "Judgments in code", owners are the owner, Sank (the template's author), a dated
  DECISIONS.md entry, or nobody (a finding).

## Extra antipatterns

- **Invented figures (rule 8):** a default, fallback or plug that puts a number into a
  model with no source and no human sign-off; a house default used on a deal input.
- **Untrusted numbers:** LibreOffice or engine figures quoted as fact (rule 1); the
  template saved through openpyxl (rule 2); a source `.xlsx` modified (rule 3).
- **Consistency instead of an independent answer (rule 4):** "QC PASS and plausible"
  offered as proof that returns are right; the oracle, a golden model or propagation is
  the answer.
- **Tests that copy the code (rule 5):** expectations derived through the code under
  test, or mocks asserted as called.
- **Unchecked input (rule 6):** a new schema input with no `esr verify` check and no
  exemption with a reason.
- **Template workarounds untagged (rule 7):** coping code without `TEMPLATE-PENDING`
  and its README row.
- **Typed values flattened at the Windows boundary:** the PowerShell helper and
  `esr-dev native`. An argument list joined into a command line, a path reduced to a
  filename, or a status recovered from printed text; carry it in the spec or the record.
- **Real processes:** anything that can start, stop or signal a process outside the test
  harness's audit-hook watch (`tests/test_office.py`).
- **Code hygiene:** comments that stand in for DECISIONS.md; a file over 600 lines; a
  ratchet count in `tests/lint_baseline.json` that went up.
- **Unneeded backward compatibility** (shared item; esr is greenfield, owner
  2026-10-08). The likely forms here: the Artemis template path, a superseded Sank
  template version, bare `base_case` key aliases, old CLI forms, and old native-run or
  evidence record formats.
