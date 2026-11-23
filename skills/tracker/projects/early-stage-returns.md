# tracker adapter: early-stage-returns (esr)

The repository's CLAUDE.md is binding and wins over this file. This holds only esr's
specifics; generic orchestration policy stays in the skill.

## The deal corpus

A change to a house default or to a question `esr assumptions plan` asks is proven on
the corpus before the PR goes out (tracker; owner 2026-11-23: "the #94 → #100 reversal
shows what skipping it costs").

- The corpus is `~/scratch/run20/NN/` (deals 01–20): `team.yaml` (the deal team's
  inputs), `cim.yaml` (a CIM-only user's), `expected.md` and `findings.md`;
  `targets.tsv` holds each deal team's IRR / MOIC. Deals 21–40 join it from
  `~/scratch/run40/` once their native runs are done.
- Run each file natively at the base sha and at the head sha with `run.sh` (Excel queue,
  `esr model` then `esr deck`), into a fresh copy of the corpus per sha: `run.sh` skips
  every file whose `.done` marker exists. `table.sh` prints one sha's table.
- The table has one row per file: deal team, esr at base, esr at head, and why it
  moved. A file the change cannot reach is still listed, unchanged.
- Deal names and figures are confidential (CLAUDE.md): the table is posted with
  `owner-dash put report pr<N>-corpus <file>`, and the PR body says it was run, at
  which shas, and which deal numbers moved.
