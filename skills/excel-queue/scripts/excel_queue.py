r"""The machine-wide Excel queue: one Excel automation at a time on this desktop, served in arrival order.

Source: skills/excel-queue/scripts/excel_queue.py in the dev-skills repository, its one home; the skill's SKILL.md
says when and how to use it. Every session runs it from a dev-skills checkout; there is no installed copy.

Sessions whose automation Excels overlap on one desktop collide: one run waited 13 hours, an earlier one hung in
Workbooks.Open until Excel was ended by hand. So every session takes a turn before it starts Excel and gives it
back after Excel quits. In Python (stdlib only):

    with excel_turn("what the turn is for"):
        ...start Excel, use it, quit it...

From any shell:

    python3 excel_queue.py run [--purpose TEXT] [--timeout SECONDS] -- <command...>
        take a turn, run the command, give the turn back; exits with the command's exit code
        (75 when --timeout passes before the turn comes; the command is then never started)
    python3 excel_queue.py status                  who holds Excel and who is waiting
    python3 excel_queue.py release --stale-only    break the lock only when its holder is stale

The queue folder is C:\ProgramData\excel-queue on Windows and /mnt/c/ProgramData/excel-queue from WSL;
EXCEL_QUEUE_DIR overrides it. In it:

    lock/holder.json          the holder: who, host, pid, purpose, arrived, started, heartbeat, ticket. The lock
                              is the directory: os.mkdir creates it for exactly one caller.
    tickets/<utc>-<id>.json   one per session in line, and the holder's until it releases, each with its own
                              heartbeat. Only the oldest live ticket may take the lock, so turns go in arrival order.
    stale-<utc>-<id>/         a broken stale lock, its holder.json kept for inspection.
    queue.log                 one line per broken lock and per removed stale ticket.

A session refreshes its heartbeats every HEARTBEAT_SECONDS from a daemon thread, waiting or holding; one silent
for STALE_SECONDS is stale. A waiter removes a stale ticket, and the first in line breaks a stale lock, only once it
has seen the same record on CONFIRM_POLLS polls in a row (confirmed_stale), so a live writer whose clock runs behind
(WSL's and Windows' clocks can differ) is never removed or broken: its heartbeat keeps changing. Arrival order is
each writer's own clock: a session whose clock is X behind goes ahead of anyone who arrived up to X earlier.
Waiting prints one line, then is quiet.
A nested excel_turn in the same thread joins the turn it is in; other threads count as other sessions.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

HEARTBEAT_SECONDS = 30.0
STALE_SECONDS = 600.0
POLL_SECONDS = 1.0
CONFIRM_POLLS = 90
RETRIES = 40
RETRY_SECONDS = 0.05
TIMEOUT_EXIT = 75
PURPOSE_CHARS = 300
SCRIPT = "excel_queue.py"
LOCK = "lock"
HOLDER = "holder.json"
TICKETS = "tickets"
LOG = "queue.log"
NAME_TIME = "%Y%m%dT%H%M%S.%fZ"
RECORD_TIME = "%Y-%m-%dT%H:%M:%S.%fZ"


class QueueTimeout(TimeoutError):
    """The turn did not come within the timeout. The ticket is withdrawn and Excel was never started."""


def queue_dir() -> Path:
    """EXCEL_QUEUE_DIR when set; else ProgramData's excel-queue, through the C: mount when not on Windows."""
    override = os.environ.get("EXCEL_QUEUE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "excel-queue"
    return Path("/mnt/c/ProgramData/excel-queue")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(RECORD_TIME)


def ascii_text(text: object) -> str:
    return str(text).encode("ascii", "replace").decode("ascii")


def whoami() -> str:
    """The user, and whether the session runs in WSL (which distribution) or on Windows."""
    try:
        user = getpass.getuser()
    except (OSError, ImportError, KeyError):
        user = "unknown"
    distro = os.environ.get("WSL_DISTRO_NAME")
    if distro:
        return f"{user} (WSL {distro})"
    return f"{user} (Windows)" if sys.platform == "win32" else user


def retried(action: Callable[..., object], *paths: Path) -> bool:
    """Run a file operation on `paths`: True when done, False when its file was already gone. Windows refuses to
    replace or delete a file another process has open, so a refusal is retried for a moment before it is raised."""
    for attempt in range(RETRIES):
        try:
            action(*paths)
            return True
        except FileNotFoundError:
            return False
        except PermissionError:
            if attempt + 1 == RETRIES:
                raise
            time.sleep(RETRY_SECONDS)
    return False


def write_json(root: Path, path: Path, record: dict) -> None:
    """Write `record` to `path` whole: a temporary file in `root`, swapped in, so a reader never sees part of one."""
    staged = root / f".tmp-{uuid.uuid4().hex}"
    staged.write_text(json.dumps(record), encoding="ascii")
    try:
        retried(os.replace, staged, path)
    finally:
        retried(os.unlink, staged)


def read_json(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def parse_time(stamp: object) -> float | None:
    """A record's time (RECORD_TIME, UTC) as a POSIX timestamp; None when it has none."""
    try:
        return datetime.strptime(str(stamp), RECORD_TIME).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def observed(record: dict | None, changed: float) -> dict:
    """`age` since the record's heartbeat (since `changed`, a modification time, when it has none), `stale`, and
    `seen`, which identifies what was observed, for confirmed_stale."""
    beat = parse_time((record or {}).get("heartbeat"))
    age = time.time() - (changed if beat is None else beat)
    seen = json.dumps(record, sort_keys=True) if record else f"changed {changed}"
    return {"age": round(age, 1), "stale": age > STALE_SECONDS, "seen": seen}


def read_holder(root: Path) -> dict | None:
    """The holder's record with what `observed` adds and `held_minutes`, from its own start to its own last heartbeat
    (one clock, so no skew); None when nobody holds the lock. A lock whose holder.json cannot be read (not yet
    written, or being released) is judged by when the lock last changed."""
    lock = root / LOCK
    record = read_json(lock / HOLDER)
    try:
        changed = lock.stat().st_mtime
    except OSError:
        return None
    record = record or {}
    started, beat = parse_time(record.get("started")), parse_time(record.get("heartbeat"))
    held = None if started is None or beat is None else round((beat - started) / 60, 1)
    return {**record, **observed(record or None, changed), "held_minutes": held}


def holder_ticket(lock: Path) -> str | None:
    """The ticket the lock's holder.json names; None when the lock has no holder.json."""
    path = lock / HOLDER
    for _ in range(RETRIES):
        record = read_json(path)
        if record is not None:
            return str(record.get("ticket"))
        if not path.exists():
            return None
        time.sleep(RETRY_SECONDS)
    return None


def read_tickets(root: Path) -> list[dict]:
    """Every ticket in arrival order (by name), with its heartbeat `age` and `stale`."""
    tickets = []
    for path in sorted((root / TICKETS).glob("*.json")):
        record = read_json(path)
        try:
            changed = path.stat().st_mtime
        except OSError:
            continue
        tickets.append({**(record or {}), "ticket": path.stem, **observed(record, changed)})
    return tickets


def status(root: Path | None = None) -> dict:
    """The queue folder, the holder (None when free), the sessions waiting in arrival order, the broken locks kept."""
    root = root or queue_dir()
    holder = read_holder(root)
    held = holder.get("ticket") if holder else None
    return {"dir": str(root),
            "holder": {k: v for k, v in holder.items() if k != "seen"} if holder else None,
            "queue": [{k: v for k, v in t.items() if k != "seen"} for t in read_tickets(root) if t["ticket"] != held],
            "stale_locks": sorted(p.name for p in root.glob("stale-*"))}


def describe(record: dict) -> str:
    return f"{record.get('who', '?')} on {record.get('host', '?')} (pid {record.get('pid', '?')})"


def short_time(stamp: object) -> str:
    return f"{str(stamp)[:19].replace('T', ' ')}Z" if stamp else "?"


def format_status(state: dict) -> list[str]:
    """`status` as ASCII lines for a console."""
    def entry(record: dict, since: str) -> str:
        return (f"{describe(record)} since {short_time(record.get(since))}, heartbeat {record['age']:.0f}s ago"
                f"{', STALE' if record['stale'] else ''}: {record.get('purpose', '?')}")
    holder = state["holder"]
    held = f" (held {holder['held_minutes']:.0f} min)" if holder and holder.get("held_minutes") is not None else ""
    lines = [f"excel-queue {state['dir']}",
             f"holder: {entry(holder, 'started')}{held}" if holder else "holder: none, Excel is free",
             f"waiting: {len(state['queue'])}"]
    lines += [f"  {n}. {entry(t, 'arrived')}" for n, t in enumerate(state["queue"], 1)]
    if state["stale_locks"]:
        lines.append(f"broken stale locks kept: {', '.join(state['stale_locks'])}")
    return [ascii_text(line) for line in lines]


def note(root: Path, text: str) -> None:
    """Say `text` on stderr and append it, timed, to the queue's log."""
    print(ascii_text(f"excel-queue: {text}"), file=sys.stderr, flush=True)
    try:
        with open(root / LOG, "a", encoding="ascii") as fh:
            fh.write(ascii_text(f"{utc_now()} {text}") + "\n")
    except OSError:
        pass


def confirmed_stale(observation: dict, seen: dict) -> bool:
    """True once the same stale record (a holder's or a ticket's) has been seen on CONFIRM_POLLS polls in a row;
    `seen` carries the count from one poll to the next. A live writer whose clock runs behind still changes its
    heartbeat every HEARTBEAT_SECONDS, so it is never confirmed."""
    if not observation["stale"]:
        seen.clear()
        return False
    count = seen.get(observation["seen"], 0) + 1
    seen.clear()
    seen[observation["seen"]] = count
    return count >= CONFIRM_POLLS


def break_lock(root: Path, holder: dict, by: str) -> bool:
    """Rename the stale lock that `holder` confirmed to stale-<utc>-<id>, kept for inspection, and log it; only when
    the lock still holds that same record (ticket and heartbeat), so a lock taken since is left alone. False when it
    does not, or the rename failed (the lock is gone, or another process has a file in it open); the caller looks
    again on its next poll. The check and the rename are two steps: a lock released and taken again between them
    would still be renamed, a window of microseconds after ten minutes of silence."""
    current = read_holder(root)
    if current is None or current["seen"] != holder["seen"]:
        return False
    kept = f"stale-{datetime.now(timezone.utc).strftime(NAME_TIME)}-{uuid.uuid4().hex[:8]}"
    try:
        os.rename(root / LOCK, root / kept)
    except OSError:
        return False
    note(root, f"{by} broke the stale lock of {describe(holder)} (heartbeat {holder['age']:.0f}s old), "
               f"for: {holder.get('purpose', '?')}; kept as {kept}")
    return True


class Turn:
    """One session's place in the queue: a ticket, then the lock. A daemon thread refreshes both heartbeats."""

    def __init__(self, purpose: str, root: Path | None = None):
        self.root = root or queue_dir()
        self.name = f"{datetime.now(timezone.utc).strftime(NAME_TIME)}-{uuid.uuid4().hex}"
        self.ticket = self.root / TICKETS / f"{self.name}.json"
        self.lock = self.root / LOCK
        now = utc_now()
        self.who = whoami()
        self.record = {"who": self.who, "host": socket.gethostname(), "pid": os.getpid(),
                       "purpose": ascii_text(purpose)[:PURPOSE_CHARS], "arrived": now, "heartbeat": now,
                       "ticket": self.name}
        self.holding = False
        self._writing = threading.Lock()
        self._stop = threading.Event()
        self._beat = threading.Thread(target=self._heartbeat, name="excel-queue heartbeat", daemon=True)
        self._watching: dict[str, dict] = {}

    def take(self, timeout: float | None = None) -> None:
        """Wait in line until this turn holds the lock; QueueTimeout after `timeout` seconds (None: no limit)."""
        self.ticket.parent.mkdir(parents=True, exist_ok=True)
        self._write(self.ticket)
        self._beat.start()
        deadline = None if timeout is None else time.monotonic() + timeout
        seen: dict = {}
        announced = False
        while True:
            holder, line = self._line()
            place = line.index(self.name) + 1
            if place == 1:
                if self._lock():
                    return
                if holder and confirmed_stale(holder, seen) and break_lock(self.root, holder, self.who):
                    continue
            held = (f"held by {describe(holder)} since {short_time(holder.get('started'))} for: "
                    f"{holder.get('purpose', '?')}" if holder else "free, with earlier arrivals ahead")
            where = ascii_text(f"{held}; place in line {place} of {len(line)}")
            if not announced:
                print(f"excel-queue: waiting for Excel, {where}", file=sys.stderr, flush=True)
                announced = True
            if deadline is not None and time.monotonic() >= deadline:
                raise QueueTimeout(f"excel-queue: no turn within {timeout:g}s, {where}")
            time.sleep(POLL_SECONDS)

    def release(self) -> None:
        """Give the turn back: stop the heartbeat, then remove holder.json, the lock and the ticket."""
        self._stop.set()
        if self._beat.is_alive():
            self._beat.join()
        try:
            if self.holding and holder_ticket(self.lock) == self.name:
                retried(os.unlink, self.lock / HOLDER)
                retried(os.rmdir, self.lock)
        finally:
            self.holding = False
            retried(os.unlink, self.ticket)

    def _write(self, path: Path, **changes: str) -> None:
        with self._writing:
            self.record.update(changes, heartbeat=utc_now())
            write_json(self.root, path, self.record)

    def _line(self) -> tuple[dict | None, list[str]]:
        """The holder, and the live tickets in arrival order without the holder's. Removes a ticket once it is
        confirmed stale (confirmed_stale, per ticket), and puts this session's ticket back under its own name, so in
        its own place, when another session removed it."""
        holder = read_holder(self.root)
        held = holder.get("ticket") if holder else None
        line, watching = [], {}
        for ticket in read_tickets(self.root):
            name = ticket["ticket"]
            if name == held:
                continue
            if name != self.name:
                watching[name] = self._watching.get(name, {})
                if confirmed_stale(ticket, watching[name]):
                    self._remove(ticket)
                    continue
            line.append(name)
        self._watching = watching
        if self.name not in line:
            self._write(self.ticket)
            line = sorted(line + [self.name])
        return holder, line

    def _remove(self, ticket: dict) -> None:
        try:
            removed = retried(os.unlink, self.root / TICKETS / f"{ticket['ticket']}.json")
        except OSError:
            return
        if removed:
            note(self.root, f"{self.who} removed the stale ticket of {describe(ticket)} (heartbeat "
                            f"{ticket['age']:.0f}s old), for: {ticket.get('purpose', '?')}")

    def _lock(self) -> bool:
        try:
            os.mkdir(self.lock)
        except OSError:
            return False
        try:
            self._write(self.lock / HOLDER, started=utc_now())
        except BaseException:
            retried(os.rmdir, self.lock)
            raise
        self.holding = True
        return True

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self._write(self.ticket)
                if not self.holding:
                    continue
                if holder_ticket(self.lock) == self.name:
                    self._write(self.lock / HOLDER)
                else:
                    self.holding = False
                    note(self.root, f"the lock of {self.who} was broken while it held Excel, "
                                    f"for: {self.record['purpose']}")
            except OSError:
                continue


_held = threading.local()


@contextmanager
def excel_turn(purpose: str, timeout: float | None = None) -> Iterator[Turn]:
    """Hold the machine-wide Excel turn for the block: wait in line, hold, give it back on the way out, whatever
    happens. QueueTimeout when `timeout` seconds pass first. Inside a turn, a nested excel_turn in the same thread
    joins it."""
    inside = getattr(_held, "turn", None)
    if inside is not None:
        yield inside
        return
    turn = Turn(purpose)
    try:
        turn.take(timeout)
        _held.turn = turn
        yield turn
    finally:
        _held.turn = None
        turn.release()


def run_in_turn(command: list[str], purpose: str | None = None, timeout: float | None = None) -> int:
    """Take a turn, run `command`, give the turn back. The command's exit code (128 + N when signal N ended it),
    127 when it could not start, TIMEOUT_EXIT when the turn did not come in time."""
    try:
        with excel_turn(purpose or " ".join(command), timeout):
            try:
                code = subprocess.call(command)
            except OSError as e:
                print(ascii_text(f"excel-queue: could not start {command[0]}: {e}"), file=sys.stderr, flush=True)
                return 127
    except QueueTimeout as e:
        print(e, file=sys.stderr, flush=True)
        return TIMEOUT_EXIT
    return 128 - code if code < 0 else code


def release_stale(root: Path | None = None) -> int:
    """Break the lock only when its holder is stale, confirmed as a waiter confirms it. 0 when it was broken or
    nobody holds it; 1 when the holder is live."""
    root = root or queue_dir()
    seen: dict = {}
    while True:
        holder = read_holder(root)
        if holder is None:
            print("excel-queue: nobody holds the lock; nothing to break.", flush=True)
            return 0
        if not holder["stale"]:
            print(ascii_text(f"excel-queue: {describe(holder)} holds the lock and is live (heartbeat "
                             f"{holder['age']:.0f}s ago); nothing broken."), flush=True)
            return 1
        if not seen:
            print(f"excel-queue: the lock looks stale; watching it for {CONFIRM_POLLS} polls before breaking it.",
                  flush=True)
        if confirmed_stale(holder, seen) and break_lock(root, holder, whoami()):
            return 0
        time.sleep(POLL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=SCRIPT, description="The machine-wide Excel queue: one Excel "
                                     "automation at a time on this desktop, in arrival order.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="who holds Excel and who is waiting")
    turn = sub.add_parser("run", help="take a turn, run the command, give the turn back; exits with its code")
    turn.add_argument("--purpose", help="what the turn is for, shown to those waiting (default: the command)")
    turn.add_argument("--timeout", type=float, metavar="SECONDS",
                      help=f"give up after waiting this long and exit {TIMEOUT_EXIT} (default: wait indefinitely)")
    turn.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command to run")
    release = sub.add_parser("release", help="break the lock of a stale holder")
    release.add_argument("--stale-only", action="store_true", required=True,
                         help="required: only a stale holder's lock is broken, never a live one")
    args = parser.parse_args(argv)
    if args.cmd == "status":
        print("\n".join(format_status(status())), flush=True)
        return 0
    if args.cmd == "release":
        return release_stale()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("run needs the command to run after --")
    return run_in_turn(command, args.purpose, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
