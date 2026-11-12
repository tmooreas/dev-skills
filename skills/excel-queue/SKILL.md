---
name: excel-queue
description: Take a turn in the machine-wide Excel queue before starting or driving Excel through COM on the shared Windows desktop, so sessions never run their automation Excels at once; served in arrival order. Use for "excel queue", "who is using Excel", "is Excel free", "run something in Excel", "wait for Excel", or before any Excel-COM job.
---

# Excel queue

Before starting, bring the skills up to date by running:

    "$(git -C <this dir> rev-parse --show-toplevel)/bin/skills-sync"

It only fast-forwards, is safe beside other running sessions, and prints one line.
If it updated, re-read this file; if it did not, say so in your first line and
continue with the version you have.

Several sessions (different WSL users, and native Windows runs) drive Excel through
COM on one Windows desktop. Their automation Excels collided: one run waited 13 hours,
another hung in `Workbooks.Open` until Excel was ended by hand. So every Excel job takes
a turn in one queue first: one holder at a time, the rest wait in arrival order.

## When to use it

Any session that starts or drives Excel through COM on this desktop, whatever the
tool: PowerShell, pywin32, a native Windows Python, a script that recalculates or
exports a workbook. PowerPoint and openpyxl (no Excel process) do not need a turn.

## Wrap the job

    python3 /mnt/c/CodeProjects/dev-skills/skills/excel-queue/scripts/excel_queue.py run --purpose "<what this job is for>" -- <command and its arguments>

It waits for the turn, printing one line when it starts waiting (who holds Excel,
since when, for what, your place in line), runs the command, gives the turn back when
the command ends, even on failure, and exits with the command's exit code.
`--timeout SECONDS` gives up instead of waiting indefinitely: exit 75, and the command
is never started. A native Windows Python runs the same file as
`C:\CodeProjects\dev-skills\skills\excel-queue\scripts\excel_queue.py`. Python code can
hold a turn in-process: with `scripts/` on `sys.path`, `from excel_queue import excel_turn`,
then `with excel_turn("purpose"):` around starting, using and quitting Excel.

## Who is using Excel

    python3 /mnt/c/CodeProjects/dev-skills/skills/excel-queue/scripts/excel_queue.py status

prints the holder (who, host, pid, since when, last heartbeat, how many minutes it has
held, its purpose) and each session waiting, in order.

## Rules

- One turn for a whole Excel job, from starting Excel to quitting it, not one per call.
  The command must not return before its Excel has quit (no detached `Start-Process`).
- Never start Excel outside a turn.
- Never edit or delete anything in the queue folder, `C:\ProgramData\excel-queue`
  (`/mnt/c/ProgramData/excel-queue` from WSL). The script keeps it: any waiter removes a
  ticket whose heartbeat is confirmed stopped, only the first in line breaks a stale lock,
  and a broken lock is kept as `stale-<utc>-<id>/` with a line in `queue.log`.
- To clear a lock by hand, use only `release --stale-only`: it breaks a lock only when
  its holder has been silent for 10 minutes, never a live one.
- Run the script from the dev-skills checkout; edit it only here, in this repository,
  with its tests.

## How it works

The lock is the directory `lock/`, made with `os.mkdir`, which exactly one caller wins;
`lock/holder.json` names the holder. Each session first writes a ticket,
`tickets/<utc>-<uuid>.json`, and may take the lock only when its ticket is the oldest
live one. A daemon thread refreshes the session's heartbeats every 30 s. A heartbeat
silent for 10 minutes is stale, but a waiter acts on it only after seeing the same
record unchanged on 90 polls in a row (about 90 s): a live writer on a clock that runs
behind keeps changing its heartbeat and is never removed. A lock is broken only while
it still holds the record that was confirmed.

## Known limits

- A hung but live holder (Excel hangs while the holder's heartbeat thread keeps going)
  holds the turn until it is ended by hand. `status` shows how long it has held.
- Arrival order is each writer's own clock: a session whose clock is X behind goes
  ahead of anyone who arrived up to X earlier. There is no shared clock.
- Breaking a lock checks the record, then renames: a lock released and taken again in
  the microseconds between the two steps would still be renamed.

## Verify

    python3 skills/excel-queue/tests/test_excel_queue.py

Stdlib unittest: sessions are threads on a temporary queue folder (`EXCEL_QUEUE_DIR`);
no Excel, no Windows process.
