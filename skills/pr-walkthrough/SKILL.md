---
name: pr-walkthrough
description: Walk the owner through one PR so they can approve it without opening it - what it does, why it is needed, an ASCII diagram of the flow, how it works concretely in the code, proof it works, and an honest antipattern check that stops and says so if one is found. Use for "walk me through #N", "pr walkthrough N", "justify #N".
---

# PR walkthrough

The owner approves merges. A walkthrough lets them do that from the chat alone:
plain language first, then a picture, then the code, then the proof, then the
antipattern check. Read-only: no edits, pushes, GitHub writes, deployments or
long-running jobs. Read-only status commands and GitHub reads are fine.

Input: a PR number. Everything below is about that PR at its **current head**.

## Project adapter

Before anything else, check the skill and find the adapter:

1. **Is this skill current?** If this directory is inside a git checkout, run
   `git -C <this dir> fetch -q` and `git -C <this dir> status -sb`. If it is behind its
   upstream, fast-forward it (`git -C <this dir> pull --ff-only`) and re-read this file.
   If it can't fast-forward (local changes, no network), say so in the walkthrough's
   first line and continue with the version you have.
2. **Read the adapter.** Look for `projects/<repository-name>.md` next to this file. If
   it exists, read it: it names the project's status and diff commands, its proof
   sources, extra sections and extra antipatterns, and its safety limits. The adapter
   adds to this skill and overrides it where they conflict; the project's own CLAUDE.md
   or AGENTS.md overrides both. With no adapter, resolve commands and policy from the
   project's instructions.
3. **Does the adapter still fit?** Check that every section number, heading and command
   the adapter names still exists in this skill and the project. If one doesn't, say so
   in the walkthrough's first line, follow this skill for that part, and propose the
   adapter fix at the end. New general rules go in this file; project rules go in the
   adapter. Never copy either into a project's own skills.

Check the adapter against this skill every run, before using it: every section number
it refers to exists here with the same meaning, every command and path it names still
exists in the project, and none of its rules contradicts a rule here without saying it
overrides it. Report any drift in one line at the top of the walkthrough and follow
this skill where the adapter is stale. Generic improvements go in this file, never in
an adapter; an adapter holds only what is specific to its project.

## Prepare (before writing a word)

1. Pin the facts live: `gh pr view N --json headRefOid,baseRefName,isDraft,state,additions,deletions,statusCheckRollup,body`,
   or the adapter's status command. State the head SHA you read. Never describe a head
   from memory or from an older review.
2. Read the **diff at that head** (`git diff <base>...<head>`), not only the PR body.
   The body says what the author meant; the diff is what ships.
3. Read the code around each changed hunk: the callers, what produces each input the
   change consumes, what consumes each output it produces.
4. Read the latest independent review verdict for this head, and the orchestrator's own
   smoke output if there is one. Note which head each covers.
5. Run the antipattern check below while preparing, not after writing. When a finding
   rests on behaviour, reproduce it with a throwaway script that starts nothing outside
   your sandbox. The finding then carries an observed output, not a reading of the code.

## If you find an antipattern

Stop the walkthrough at that point and say it plainly:

> **Oh fuck, wait, actually we need to change this.**

Then say four things:
- what it is (file:line);
- a concrete input that goes wrong, and what the user would see;
- why the review missed it, if it did;
- the smallest fix that removes the pattern, not a narrower special case.

Do not finish the walkthrough as if the PR were approvable. End with the proposed fix
brief for owner approval.

## Output

Keep each section short; the owner can ask for more.

1. **In one sentence:** what changes for the person running the pipeline or reading its
   output. No function names.
2. **Why we need it:** the problem in the owner's terms, with the concrete incident (the
   input, the number, the review finding, the owner's quote and its date). Say what
   happens today without it.
3. **What it does, step by step:** the flow in plain words, then an **ASCII diagram**.
   It is always present, even for a one-liner (then it is two boxes). Use plain ASCII
   only (`+--+`, `|`, `-->`, `v`), no mermaid and no Unicode box characters, and keep it
   under 80 columns.
   - Show the path the PR adds or changes, from input to what the user sees.
   - Mark new or changed boxes with `*`, and refusals with an `X-->` branch naming what
     is refused.
   - Where it helps, show before and after side by side.
4. **How it works in the code:** the 3-6 places that matter, each as `file:line` plus
   one sentence on what that code does and why it is written that way. Name what each
   changed piece reads (its producer) and what reads it (its consumer).
5. **A worked example:** real numbers from a real run (the orchestrator's smoke, or run
   it now): input -> what the code did -> what the user sees. Include one refusal or
   edge case.
6. **What it removes or replaces:** old code deleted, old behaviour gone, migrated
   callers. Say if anything old is left behind and why.
7. **Proof:** the review verdict (reviewer, head, PASS/BLOCKED), CI on this head, the
   tests that would fail without the change (name them), and the smoke. Say what was not
   exercised.
8. **Do the tests earn their place?** For each test the PR adds or changes, answer
   three questions:
   - **Does it prove what we want proved?** Name the behaviour the owner cares about and
     show the test would fail if that behaviour broke. Prefer a mutation you ran, or the
     test failing on the base commit. A test that passes whatever the code does proves
     nothing.
   - **Does it add anything?** Say what it covers that no other test or check already
     covers. Duplicates, tautologies, "does not throw" and tests of wiring or wording
     are cost, not proof; recommend deleting them.
   - **Does it codify a bug?** Check every expected value against an independent answer
     (worked by hand, the domain rule, the authority system). An expectation copied from
     what the code currently outputs, a golden rebaked to match, or a test asserting
     behaviour that should not exist (a silent default, a wrong sign, a swallowed
     error) makes the bug permanent. If you find one, stop as for an antipattern.
9. **Antipattern check:** go through every item below, plus the adapter's, and say
   "clean" with the reason, or stop as above. Under "Judgments in code:", list each place
   the code decides something and who owns that decision.
10. **Limits and follow-ups:** known gaps, what is deliberately out of scope, what comes
    next and in which PR.
11. **Size and order:** changed lines vs 800, what it is stacked on, what waits on it,
    merge order.
12. **Ask:** the one decision you need, if any (approve the merge, or a choice), with
    your recommendation.

An adapter may add sections; it numbers them after the section they follow (9a, 9b), so
the numbers above stay stable.

## Antipattern checklist

Check each item against the diff.

- **Code deciding what data means:** regex or keyword lists over prose, labels, column
  or sheet names; vocabulary lists; magnitude or unit thresholds; guessing from names.
  The model judges meaning; code validates typed values.
- **Events recognized from printed text:** anything that parses the system's own
  output (banners, log lines, SVG text, number formats) instead of reading a typed record.
- **Structure dropped, then re-parsed:** a typed value (a record field, a key, an id, a
  cell address, a unit, a date, a source) gets flattened or dropped on the way through:
  into prose, a log line, a formatted string, a filename or a summary. Then a later step
  needs it and recovers it with a parser over that text, which breaks on the first
  unusual case. Trace each value the change consumes back to where it was last typed.
  If a typed form existed upstream, the fix is to carry it through (an output field, a
  record key), never to parse it back. Flag it as well when the change itself drops a
  field that a known later consumer will need.
- **Consumes what nothing produces:** every input the change reads has a real producer
  on main or in this PR's stack.
- **Positions instead of keys:** row/column offsets, sheet order, list index where an id
  or key exists.
- **Fail-open:** missing, unknown or unparseable input silently becomes a default, zero,
  "full", PASS or an empty list instead of a refusal or NA. This includes evidence that
  was named but not found.
- **Duplicate owner:** a second home for a rule, list or parser that already exists
  (check with the project's inventory tool, grep or LSP references).
- **Special cases:** behaviour keyed to a test fixture, a customer name or one incident.
- **Tests that pin wording/wiring:** asserting message text, formula strings or call
  forwarding instead of behaviour; goldens rebaked to hide a change.
- **Dead or half-done code:** stubs, unused flags, compatibility shims, unfinished
  cutover.

## Rules

- Plain language; the owner should not need to decode.
- Every claim about code has a `file:line`; every claim about behaviour has an observed
  output or is marked `[INFERENCE]`.
- A PASS from the reviewer is evidence, not the walkthrough: re-check the antipatterns
  yourself.
- If the head moved since the review or smoke, say so first.
- Every section is present for every PR, a move or one-liner included: shorten a
  section, never drop it. A move still needs its own worked example (the same input
  through the old and the moved code, same output) and a line-count explanation (lines
  added vs deleted: moved code, new docs, new tests, wiring), because a move that grows
  is the first thing the owner will ask about.
