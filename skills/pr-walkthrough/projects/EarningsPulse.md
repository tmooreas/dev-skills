# pr-walkthrough adapter: EarningsPulse (ep)

The repository's CLAUDE.md and AGENTS.md are binding and win over this file. EarningsPulse
stages earnings releases, SEC XBRL and market data per run, drafts a digest (email, KFM
table, exhibit deck), checks it, and delivers it to subscribers. The owner's rulings are
in CLAUDE.md, DECISIONS.md and the agent's standing rules; this file points at them and
does not restate their full text.

## Safety

- No paid calls: no extraction, audit, eval or model run with a spend flag. A command's
  `$0` dry run is fine.
- Some dry runs write into the live runs root (`runs/_eval/...`). Remove what the
  walkthrough wrote and say so.
- Read the production containers only (`docker exec ... sh -c 'ls/cat'`). Never restart,
  redeliver, release, restage or rebake.
- Free read-only SEC GETs only with `SEC_USER_AGENT` set, at most 1 per second, saved
  under `~/.cache`.

## Prepare

- Status: `gh pr view N --json headRefOid,baseRefName,mergeStateStatus,statusCheckRollup,additions,deletions,body`.
  Diff: `git diff <merge-base>...<head>` in a scratch worktree under `~/.cache` (never
  `/tmp`, which a reboot wipes); remove it at the end.
- Exported symbols: LSP references where available, else `scripts/code_inventory.py
  --grep <name>` plus grep.
- Reviews: the PR's "Independent GPT review" comments and the reports under
  `~/.cache/ep-specs/`. Only a GPT/Codex verdict counts. A Claude or Opus verdict on
  Claude-written code is self-grading: say so, and don't present it as a PASS.
- Read typed records (`RunInputs`, the run's typed artifacts, ledger rows), never log or
  printed lines.

## Output changes

- Section 2 names the run (`TICKER_Qn_FYyyyy__id`), the figure and its value where there
  is one, and the owner's quote with its date.
- Section 3's diagram runs from the source (release, XBRL filing, vendor feed) through
  staging, drafting and the checks to the email, KFM table or exhibit.
- Section 5 uses a real run: the replay (`ep-dev replay-compare --base origin/main --head
  <sha>`), the digest sweep, or a `$0` run of the changed command. Quote figures from the
  staged files or the rendered surface, never from the code's own constants.
- Section 7 adds the GPT review with passes 1c (second owners) and 1d (extraction
  antipatterns) itemized, the replay result, the digest-sweep counts with their cause, and
  any paid held-out sample (its cap and spend).
- Section 7 also states the forward-looking ruling: changes on new runs are the target;
  historical runs are regression evidence only, so report their changes without treating
  them as goals or blockers.
- Section 9: under "Judgments in code", each judgment carries its check kind: STRUCTURE,
  IDENTITY, CROSS-SOURCE, or ACCEPT + MEASURE (with where it is measured). The owner is the
  owner (a quoted ruling), a dated DECISIONS.md entry, or nobody (a finding).
- Section 11 uses the hard cap in CLAUDE.md (1,500 changed lines today; it may drop to
  800), not 800.
- Add section **9a. Good shape**, one line each, a miss is a note, not a block:
  - Does it change the existing owner instead of adding a rule beside it?
  - Does it read what the filing or release declares instead of reconstructing it?
  - Does it withhold only the unknown, keep the known figures, and say why?
  - Is it small and one concern, with a replay proving any no-byte change?
  - Was every consumer of a changed output checked, and dead code it passed deleted?

## Extra antipatterns

Each has shipped a wrong or unverifiable number here before.

- **A period from a day count:** date subtraction against bounds (55-105, 345-385,
  75/95), `timedelta`/`.days` classification, a "+1 quarter" guess. Periods come from the
  release's printed label or model-stated window fields, SEC's `frame`/`fy`/`fp`, or an
  exact date equality (the printed period end equals the filing's declared
  `DocumentPeriodEndDate`). An exact date match is identity, not a day count.
- **A model-judged value without checked printed evidence:** a date, units, table, row or
  "no debt" call with no verbatim quote that code checks is in the release. A presence
  check proves the quote exists, not that the reading is right.
- **The prompt lets the model infer an unprinted value:** "infer", "assume", "derive",
  "reconstruct" or "estimate" in prompt or schema text for a staged figure. Unprinted
  means null.
- **A prompt fitted to its own sample:** a company name or one-off phrase added after a
  sampled miss. Prompt changes are measured on held-out paid releases.
- **A fact decided by absence:** "no line is debt" is not "no debt"; "the source doesn't
  say it" is not "the source contradicts it". Repair removes only what the source
  contradicts, with the contradicting text quoted.
- **The wrong concept or a rounded figure, silently:** total equity where parent equity
  is printed; a highlights table's rounded figure where the statement prints the exact
  one. The staged figure records its table and row.
- **A later or another filing's value:** SEC frames identify the period; the value comes
  from the event's own filing (its accession). Never mix filings when deriving. A cover
  share count prices equity only from the event's own filing.
- **A plausibility band standing in for evidence:** a ×3, ceiling or floor deciding which
  scale or count is right. Prefer declared units and identity. A band that remains is
  named, measured on the corpus (how often it fires) and owner-approved.
- **A vendor value as a source of truth:** vendor numbers (Perplexity, AlphaVantage) never
  vouch for a filed figure.
- **A staged file read outside its typed door:** a new reader of a run's staged files goes
  through `RunInputs` or the artifact's typed loader. Present-but-invalid raises; absent
  is None. `json.loads` on a run file is a finding.
- **A staged field with no consumer:** it lands with its reader or not at all.
- **Fail-open on coverage or holds:** a missing, null or corrupt `event_context` never
  means "covered"; a held run is never sent by any path; a missing hold record means held.
- **A gate checking a different object than what ships:** gates and graders build the
  delivered figures with the delivery owner (`build_headline_tiles`,
  `build_capital_structure`), never a parallel copy.
- **Edits outside the target:** repair or cleanup that changes bytes outside the span or
  data item the plan names.
- **An integrated claim id, or prose matching to link stages:** stages keep their own ids;
  repair targets data items or exact current-file text checked against its hash.
- **A second code-identity owner:** once #1236 lands, code identity comes only from
  `settings.code_identity()` (EP_CODE_VERSION, else the checkout's git SHA, else refused).
  A fresh `git rev-parse` call or a hardcoded "unknown"/"dev" default is a finding.
