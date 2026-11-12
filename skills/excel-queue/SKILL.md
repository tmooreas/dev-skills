---
name: excel-queue
description: Take a turn in the machine-wide Excel queue before starting or driving Excel through COM on the shared Windows desktop, so sessions never run their automation Excels at once; served in arrival order. Use for "excel queue", "who is using Excel", "is Excel free", "run something in Excel", or before any Excel-COM job.
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
the command ends, even on failure, and exits with the command's exit code (128 + N
when signal N ended it, 127 when it could not start).
`--timeout SECONDS` gives up instead of waiting indefinitely: exit 75, and the command
is never started. `--timeout 0` is one immediate try.
A native Windows Python runs the same file as
`C:\CodeProjects\dev-skills\skills\excel-queue\scripts\excel_queue.py`. Python code can
hold a turn in-process: with `scripts/` on `sys.path`, `from excel_queue import excel_turn`,
then `with excel_turn("purpose"):` around starting, using and quitting Excel
(`QueueTimeout` when a `timeout` passes first).

There is no wait-then-run: waiting without holding a turn leaves the next command
unprotected. Wrap the command itself with `run`.

Needs Python 3.7 or later, stdlib only. The queue folder is `C:\ProgramData\excel-queue`
(`/mnt/c/ProgramData/excel-queue` from WSL); `EXCEL_QUEUE_DIR` overrides it, which is
how the tests use a temporary one.

## Who is using Excel

    python3 /mnt/c/CodeProjects/dev-skills/skills/excel-queue/scripts/excel_queue.py status

prints the holder (who, host, pid, since when, last heartbeat, how many minutes it has
held, its purpose), whether it was abandoned, and each session waiting, in order. When
the holder is stale it also says whether an automation Excel is still running, which
blocks recovery.

## Rules

- One turn for a whole Excel job, from starting Excel to quitting it, not one per call.
  The command must not return before its Excel has quit (no detached `Start-Process`).
- A launcher that starts a detached Excel job and returns at once (esr's `esr-dev native
  run`, a `Start-Process`) must hold the turn until the job has finished: wrap the launch
  and the wait for its result together, e.g.
  `excel_queue.py run --purpose "esr native <what>" -- bash -c 'id=$(esr-dev native run ... | <run id>) && esr-dev native wait "$id"'`.
  The turn is held from WSL while the job runs on Windows. If the launching session
  dies, its turn goes stale, but it is never broken while the job's automation Excel is
  still running (owner 2026-11-12: esr itself does not take turns).
- Never start Excel outside a turn.
- Never edit or delete anything in the queue folder. The script keeps it: any waiter
  removes a ticket whose heartbeat is confirmed stopped, only the first in line breaks a
  stale lock, and a broken lock is kept as `stale-<utc>-<id>/` with a line in `queue.log`.
- To clear a lock by hand, use only `release --stale-only`: it breaks a lock only when
  its holder has been silent for 10 minutes and no automation Excel is running. When
  one is, it says so and breaks nothing: end that Excel (Task Manager) or wait.
- Run the script from the dev-skills checkout; edit it only here, in this repository,
  with its tests. There is no installed copy.

## Guarantees

- One holder at a time: the lock is a directory `os.mkdir` creates for exactly one caller.
- `run` gives the turn back only after its whole job is gone. The command runs in its own
  process group (a new session on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows). On
  SIGTERM, SIGINT or SIGHUP the wrapper forwards termination to the group (SIGTERM, or
  CTRL_BREAK_EVENT on Windows), waits up to 30 s, then kills it (SIGKILL, or terminate on
  Windows, which ends only the direct child), reaps it, and only then releases, exiting
  128 + the signal. The heartbeat runs throughout.
- A WSL job can start a Windows process that outlives its group. So after a
  signal-driven shutdown `run` checks for a running automation Excel; if one is alive,
  or the check fails, it leaves the lock in place with `"abandoned": "<reason>"` in
  `holder.json`, stops its heartbeat and exits. The lock goes stale, and recovery waits
  until that Excel is gone.
- A stale lock is never broken while an automation Excel (an `EXCEL.EXE` whose command
  line has `/automation`, i.e. COM-started; the owner's own Excel windows don't count)
  is running, or when the check cannot run: recovery fails closed. The refusal is
  printed and logged once per holder per session.
- Two breakers cannot displace a fresh holder: every break and every stale-ticket
  removal runs inside the recovery mutex and re-checks the confirmed record there, and a
  lock break compares the record again after the Excel check, just before the rename.
  No breaker's clock is trusted: a mutex is judged dead only by observation.
- A timed-out wait never starts the command, even when the turn frees up just after the
  deadline.

## How it works

The queue folder holds:

    lock/holder.json          the holder: who, host, pid, purpose, arrived, started, heartbeat,
                              ticket, and abandoned when set. The lock is the directory.
    tickets/<utc>-<id>.json   one per session in line, and the holder's until it releases,
                              each with its own heartbeat.
    recovery/breaker.json     present only during a recovery step: who is breaking, since when.
    stale-<utc>-<id>/         a broken stale lock, its holder.json kept for inspection.
    recovery-dead-<utc>-<id>/ a recovery step whose breaker died mid-step.
    queue.log                 one line per broken lock, removed ticket, refused break,
                              abandoned lock and broken recovery step.

Each session first writes a ticket and may take the lock only when its ticket is the
oldest live one. A daemon thread refreshes the session's heartbeats every 30 s, waiting
or holding, so a long job never reads stale. A heartbeat silent for 10 minutes is stale,
but a waiter acts on it only after seeing the same record unchanged on 90 polls in a row
(about 90 s): a live writer on a clock that runs behind keeps changing its heartbeat and
is never removed. A lock without a readable `holder.json` (being written or released) is
judged by when the directory last changed. `held` minutes run from the holder's own start
to its own last heartbeat, one clock, so no skew.

Recovery (breaking a lock, removing a stale ticket) is serialized by the recovery mutex:
`os.mkdir(recovery/)` with the breaker's record in it. Inside it the breaker re-reads the
record it confirmed, compares, checks for a running automation Excel (breaking a lock
only), compares again, acts, and removes the mutex. A breaker that finds the mutex taken
looks again on its next poll. A mutex is broken (and logged) only after the observer has
seen the same breaker record unchanged on 90 of its own polls in a row, the rule that
confirms a stale heartbeat; the record's own time is shown, never trusted, so a breaker
whose clock runs behind keeps its step. Taking the lock stays a plain `mkdir`, so the lock
path disappears only through its holder's release or a rename inside the mutex.

The Excel check is one PowerShell query, `Get-CimInstance Win32_Process -Filter
"Name='EXCEL.EXE'" | Select ProcessId,CommandLine | ConvertTo-Json`, run as
`powershell.exe -NoProfile -NonInteractive` and read as JSON. It runs on Windows and WSL
only; anywhere else it fails, so recovery there refuses. A waiter refused by it rechecks
every 30 s.

The deadline is monotonic. The first try is always made; after any wait, the deadline is
checked before the lock is taken and again before the command starts.

Writes are whole-file swaps (a temporary file in the queue folder, then `os.replace`), so
a reader never sees half a record; Windows refuses to replace or delete a file another
process has open, so those steps retry briefly. A `mkdir` failure other than "exists"
raises with the queue folder's path, so a misconfigured mount fails loudly instead of
waiting forever. Waiting prints one line, then is quiet. A nested `excel_turn` in the same
thread joins the turn it is in; other threads count as other sessions.

## Known limits

- A hung but live holder (Excel hangs while the holder's heartbeat thread keeps going)
  holds the turn until it is ended by hand. `status` shows how long it has held.
- Arrival order is each writer's own clock: a session whose clock is X behind goes
  ahead of anyone who arrived up to X earlier. All WSL distros share one VM clock;
  native Windows runs use the Windows clock.
- SIGKILL of the wrapper cannot be handled: its job may keep running. The heartbeat then
  stops, and recovery refuses while an automation Excel lives.
- The release of a holder already confirmed stale (silent over 10 minutes) is not inside
  the mutex. A lock released and taken again during a breaker's step is left alone
  unless that happens between the breaker's last compare and its rename.
- On Windows, the kill after the grace period ends only the direct child; the Excel
  check covers anything left.
- A breaker paused mid-step for 90 of an observer's polls loses its mutex; it then
  breaks nothing unless the pause falls between its last compare and its rename.

## Verify

    python3 skills/excel-queue/tests/test_excel_queue.py

Stdlib unittest: sessions are threads on a temporary queue folder (`EXCEL_QUEUE_DIR`),
polling every few milliseconds. A crashed or skewed session is played by the file it
leaves. `run` is exercised as another session runs it, with child Pythons as the job
(including a grandchild that ignores SIGINT, for the shutdown tests); the Excel check is
mocked. No Excel, PowerShell or other Windows process is started.
