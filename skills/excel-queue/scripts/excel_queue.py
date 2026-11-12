from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import platform
import signal
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
EXCEL_RECHECK_SECONDS = 30.0
QUERY_SECONDS = 20.0
GRACE_SECONDS = 30.0
RETRIES = 40
RETRY_SECONDS = 0.05
TIMEOUT_EXIT = 75
PURPOSE_CHARS = 300
SCRIPT = "excel_queue.py"
LOCK = "lock"
HOLDER = "holder.json"
TICKETS = "tickets"
RECOVERY = "recovery"
BREAKER = "breaker.json"
LOG = "queue.log"
NAME_TIME = "%Y%m%dT%H%M%S.%fZ"
RECORD_TIME = "%Y-%m-%dT%H:%M:%S.%fZ"
EXCEL_QUERY = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
               "Get-CimInstance Win32_Process -Filter \"Name='EXCEL.EXE'\" | Select ProcessId,CommandLine | "
               "ConvertTo-Json")
GROUP = ({"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if sys.platform == "win32"
         else {"start_new_session": True})


class QueueTimeout(TimeoutError):
    pass


class Cancelled(Exception):
    pass


class ExcelQueryError(RuntimeError):
    pass


class RecoveryRefused(RuntimeError):
    pass


def queue_dir() -> Path:
    override = os.environ.get("EXCEL_QUEUE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "excel-queue"
    return Path("/mnt/c/ProgramData/excel-queue")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(RECORD_TIME)


def stamp() -> str:
    return f"{datetime.now(timezone.utc).strftime(NAME_TIME)}-{uuid.uuid4().hex[:8]}"


def ascii_text(text: object) -> str:
    return str(text).encode("ascii", "replace").decode("ascii")


def whoami() -> str:
    try:
        user = getpass.getuser()
    except (OSError, ImportError, KeyError):
        user = "unknown"
    distro = os.environ.get("WSL_DISTRO_NAME")
    if distro:
        return f"{user} (WSL {distro})"
    return f"{user} (Windows)" if sys.platform == "win32" else user


def retried(action: Callable[..., object], *paths: Path) -> bool:
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
    try:
        return datetime.strptime(str(stamp), RECORD_TIME).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def live_automation_excels() -> list[int]:
    if sys.platform != "win32" and "microsoft" not in platform.uname().release.lower():
        raise ExcelQueryError(f"no Excel check on {sys.platform}: only Windows and WSL are supported")
    script = base64.b64encode(EXCEL_QUERY.encode("utf-16-le")).decode("ascii")
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", script],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=QUERY_SECONDS)
    except (OSError, subprocess.SubprocessError) as e:
        raise ExcelQueryError(f"the Excel check could not run: {e}") from e
    if done.returncode != 0:
        raise ExcelQueryError(f"the Excel check failed (exit {done.returncode}): "
                              f"{done.stderr.decode('utf-8', 'replace').strip()[:200]}")
    text = done.stdout.decode("utf-8", "replace").strip().lstrip("\ufeff")
    try:
        found = json.loads(text) if text else []
    except ValueError as e:
        raise ExcelQueryError(f"the Excel check returned no JSON: {text[:200]}") from e
    found = found if isinstance(found, list) else [found]
    return sorted(int(p["ProcessId"]) for p in found
                  if "/automation" in str(p.get("CommandLine") or "").lower())


def excel_blocker() -> str | None:
    try:
        pids = live_automation_excels()
    except ExcelQueryError as e:
        return str(e)
    return f"automation Excel pids {', '.join(map(str, pids))} are running" if pids else None


def observed(record: dict | None, changed: float) -> dict:
    beat = parse_time((record or {}).get("heartbeat"))
    age = time.time() - (changed if beat is None else beat)
    seen = json.dumps(record, sort_keys=True) if record else f"changed {changed}"
    return {"age": round(age, 1), "stale": age > STALE_SECONDS, "seen": seen}


def read_holder(root: Path) -> dict | None:
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
    path = lock / HOLDER
    for _ in range(RETRIES):
        record = read_json(path)
        if record is not None:
            return str(record.get("ticket"))
        if not path.exists():
            return None
        time.sleep(RETRY_SECONDS)
    return None


def read_ticket(path: Path) -> dict | None:
    record = read_json(path)
    try:
        changed = path.stat().st_mtime
    except OSError:
        return None
    return {**(record or {}), "ticket": path.stem, **observed(record, changed)}


def read_tickets(root: Path) -> list[dict]:
    tickets = (read_ticket(path) for path in sorted((root / TICKETS).glob("*.json")))
    return [t for t in tickets if t is not None]


def status(root: Path | None = None) -> dict:
    root = root or queue_dir()
    holder = read_holder(root)
    held = holder.get("ticket") if holder else None
    return {"dir": str(root),
            "holder": {k: v for k, v in holder.items() if k != "seen"} if holder else None,
            "excel": excel_blocker() if holder and holder["stale"] else None,
            "queue": [{k: v for k, v in t.items() if k != "seen"} for t in read_tickets(root) if t["ticket"] != held],
            "stale_locks": sorted(p.name for p in root.glob("stale-*"))}


def describe(record: dict) -> str:
    return f"{record.get('who', '?')} on {record.get('host', '?')} (pid {record.get('pid', '?')})"


def short_time(stamp: object) -> str:
    return f"{str(stamp)[:19].replace('T', ' ')}Z" if stamp else "?"


def refusal(holder: dict, blocker: str) -> str:
    return (f"the lock of {describe(holder)} is stale (heartbeat {holder['age']:.0f}s old), but {blocker}; "
            f"end that Excel (Task Manager) or wait")


def format_status(state: dict) -> list[str]:
    def entry(record: dict, since: str) -> str:
        return (f"{describe(record)} since {short_time(record.get(since))}, heartbeat {record['age']:.0f}s ago"
                f"{', STALE' if record['stale'] else ''}: {record.get('purpose', '?')}")
    holder = state["holder"]
    held = f" (held {holder['held_minutes']:.0f} min)" if holder and holder.get("held_minutes") is not None else ""
    lines = [f"excel-queue {state['dir']}",
             f"holder: {entry(holder, 'started')}{held}" if holder else "holder: none, Excel is free"]
    if holder and holder.get("abandoned"):
        lines.append(f"  abandoned: {holder['abandoned']}")
    if holder and holder["stale"]:
        lines.append(f"  {refusal(holder, state['excel'])}" if state["excel"] else
                     "  stale, and no automation Excel is running: the first waiter breaks it once confirmed")
    lines.append(f"waiting: {len(state['queue'])}")
    lines += [f"  {n}. {entry(t, 'arrived')}" for n, t in enumerate(state["queue"], 1)]
    if state["stale_locks"]:
        lines.append(f"broken stale locks kept: {', '.join(state['stale_locks'])}")
    return [ascii_text(line) for line in lines]


def note(root: Path, text: str) -> None:
    print(ascii_text(f"excel-queue: {text}"), file=sys.stderr, flush=True)
    try:
        with open(root / LOG, "a", encoding="ascii") as fh:
            fh.write(ascii_text(f"{utc_now()} {text}") + "\n")
    except OSError:
        pass


def confirmed_stale(observation: dict, seen: dict) -> bool:
    if not observation["stale"]:
        seen.clear()
        return False
    count = seen.get(observation["seen"], 0) + 1
    seen.clear()
    seen[observation["seen"]] = count
    return count >= CONFIRM_POLLS


class RecoveryWatch:
    def __init__(self, root: Path):
        self.root = root
        self.seen: dict = {}
        self.dead: dict | None = None

    def poll(self) -> None:
        path = self.root / RECOVERY
        record = read_json(path / BREAKER)
        try:
            changed = path.stat().st_mtime
        except OSError:
            self.seen.clear()
            self.dead = None
            return
        step = {"stale": True, "record": record,
                "seen": json.dumps(record, sort_keys=True) if record else f"changed {changed}"}
        self.dead = step if confirmed_stale(step, self.seen) else None

    def clear_dead(self) -> None:
        dead, self.dead = self.dead, None
        if dead is None:
            return
        tomb = self.root / f"recovery-dead-{stamp()}"
        try:
            os.rename(self.root / RECOVERY, tomb)
        except OSError:
            return
        record = dead["record"]
        if read_json(tomb / BREAKER) != record:
            try:
                os.rename(tomb, self.root / RECOVERY)
            except OSError:
                pass
            return
        note(self.root, f"broke the recovery step of {describe(record or {})}, begun "
                        f"{short_time((record or {}).get('since'))}: unchanged for {CONFIRM_POLLS} polls, its breaker "
                        f"stopped mid-step; kept as {tomb.name}")


@contextmanager
def recovery(root: Path, by: str, watch: RecoveryWatch) -> Iterator[bool]:
    path = root / RECOVERY
    mine = {"who": by, "host": socket.gethostname(), "pid": os.getpid(), "since": utc_now(), "id": uuid.uuid4().hex}
    try:
        os.mkdir(path)
        entered = True
    except FileExistsError:
        entered = False
    if not entered:
        watch.clear_dead()
        yield False
        return
    try:
        write_json(root, path / BREAKER, mine)
    except BaseException:
        retried(os.rmdir, path)
        raise
    try:
        yield True
    finally:
        if read_json(path / BREAKER) == mine:
            retried(os.unlink, path / BREAKER)
            retried(os.rmdir, path)


def unchanged(root: Path, holder: dict) -> bool:
    current = read_holder(root)
    return current is not None and current["seen"] == holder["seen"]


def break_lock(root: Path, holder: dict, by: str, watch: RecoveryWatch) -> bool:
    with recovery(root, by, watch) as entered:
        if not entered or not unchanged(root, holder):
            return False
        blocker = excel_blocker()
        if blocker:
            raise RecoveryRefused(blocker)
        if not unchanged(root, holder):
            return False
        kept = f"stale-{stamp()}"
        try:
            os.rename(root / LOCK, root / kept)
        except OSError:
            return False
    note(root, f"{by} broke the stale lock of {describe(holder)} (heartbeat {holder['age']:.0f}s old), "
               f"for: {holder.get('purpose', '?')}; kept as {kept}")
    return True


def remove_ticket(root: Path, ticket: dict, by: str, watch: RecoveryWatch) -> bool:
    path = root / TICKETS / f"{ticket['ticket']}.json"
    with recovery(root, by, watch) as entered:
        if not entered:
            return False
        current = read_ticket(path)
        if current is None or current["seen"] != ticket["seen"]:
            return False
        try:
            removed = retried(os.unlink, path)
        except OSError:
            return False
    if removed:
        note(root, f"{by} removed the stale ticket of {describe(ticket)} (heartbeat {ticket['age']:.0f}s old), "
                   f"for: {ticket.get('purpose', '?')}")
    return removed


class Turn:
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
        self.abandoned = False
        self.cancelled: int | None = None
        self.deadline: float | None = None
        self.tries = 0
        self._writing = threading.Lock()
        self._stop = threading.Event()
        self._beat = threading.Thread(target=self._heartbeat, name="excel-queue heartbeat", daemon=True)
        self._watching: dict[str, dict] = {}
        self._refused: set[str] = set()
        self._excel_next = 0.0
        self._recovery = RecoveryWatch(self.root)

    def expired(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def take(self, timeout: float | None = None) -> None:
        self.ticket.parent.mkdir(parents=True, exist_ok=True)
        self._write(self.ticket)
        self._beat.start()
        self.deadline = None if timeout is None else time.monotonic() + timeout
        seen: dict = {}
        announced = False
        while True:
            if self.cancelled is not None:
                raise Cancelled(self.cancelled)
            self._recovery.poll()
            holder, line = self._line()
            place = line.index(self.name) + 1
            held = (f"held by {describe(holder)} since {short_time(holder.get('started'))} for: "
                    f"{holder.get('purpose', '?')}" if holder else "free, with earlier arrivals ahead")
            where = ascii_text(f"{held}; place in line {place} of {len(line)}")
            if self.tries and self.expired():
                raise QueueTimeout(f"excel-queue: no turn within {timeout:g}s, {where}")
            if place == 1:
                self.tries += 1
                if self._lock():
                    return
                if holder and confirmed_stale(holder, seen) and self._break(holder):
                    continue
            if not announced:
                print(f"excel-queue: waiting for Excel, {where}", file=sys.stderr, flush=True)
                announced = True
            if self.expired():
                raise QueueTimeout(f"excel-queue: no turn within {timeout:g}s, {where}")
            time.sleep(POLL_SECONDS)
            self.tries = max(self.tries, 1)

    def release(self) -> None:
        self._halt()
        try:
            if self.holding and not self.abandoned and holder_ticket(self.lock) == self.name:
                retried(os.unlink, self.lock / HOLDER)
                retried(os.rmdir, self.lock)
        finally:
            self.holding = False
            retried(os.unlink, self.ticket)

    def abandon(self, reason: str) -> None:
        self._halt()
        self.abandoned = True
        if self.holding and holder_ticket(self.lock) == self.name:
            self._write(self.lock / HOLDER, abandoned=ascii_text(reason))
        note(self.root, f"{self.who} left its lock in place, abandoned: {reason}; for: {self.record['purpose']}")

    def _halt(self) -> None:
        self._stop.set()
        if self._beat.is_alive():
            self._beat.join()

    def _break(self, holder: dict) -> bool:
        if time.monotonic() < self._excel_next:
            return False
        try:
            return break_lock(self.root, holder, self.who, self._recovery)
        except RecoveryRefused as e:
            self._excel_next = time.monotonic() + EXCEL_RECHECK_SECONDS
            if holder["seen"] not in self._refused:
                self._refused.add(holder["seen"])
                note(self.root, refusal(holder, str(e)))
            return False

    def _write(self, path: Path, **changes: str) -> None:
        with self._writing:
            self.record.update(changes, heartbeat=utc_now())
            write_json(self.root, path, self.record)

    def _line(self) -> tuple[dict | None, list[str]]:
        holder = read_holder(self.root)
        held = holder.get("ticket") if holder else None
        line, watching = [], {}
        for ticket in read_tickets(self.root):
            name = ticket["ticket"]
            if name == held:
                continue
            if name != self.name:
                watching[name] = self._watching.get(name, {})
                if confirmed_stale(ticket, watching[name]) and remove_ticket(self.root, ticket, self.who,
                                                                             self._recovery):
                    continue
            line.append(name)
        self._watching = watching
        if self.name not in line:
            self._write(self.ticket)
            line = sorted(line + [self.name])
        return holder, line

    def _lock(self) -> bool:
        try:
            os.mkdir(self.lock)
        except FileExistsError:
            return False
        except OSError as e:
            raise OSError(e.errno, f"excel-queue: cannot use the queue folder {self.root}: {e.strerror}") from e
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


def trap(handler: Callable) -> Callable[[], None]:
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous = {}
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is not None:
            previous[number] = signal.signal(number, handler)

    def restore() -> None:
        for number, old in previous.items():
            signal.signal(number, signal.SIG_DFL if old is None else old)
    return restore


def group_alive(child: subprocess.Popen) -> bool:
    if child.poll() is None:
        return True
    if sys.platform == "win32":
        return False
    try:
        os.killpg(child.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def signal_group(child: subprocess.Popen, hard: bool) -> None:
    try:
        if sys.platform == "win32":
            child.terminate() if hard else child.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(child.pid, signal.SIGKILL if hard else signal.SIGTERM)
    except OSError:
        pass


def stop_group(child: subprocess.Popen) -> None:
    signal_group(child, hard=False)
    end, killed = time.monotonic() + GRACE_SECONDS, False
    while group_alive(child):
        if not killed and time.monotonic() >= end:
            signal_group(child, hard=True)
            killed = True
        time.sleep(0.05)


def run_in_turn(command: list[str], purpose: str | None = None, timeout: float | None = None) -> int:
    turn = Turn(purpose or " ".join(command))
    signals: list[int] = []

    def on_signal(signum: int, frame: object) -> None:
        signals.append(signum)
        turn.cancelled = signals[0]
    restore = trap(on_signal)
    try:
        try:
            turn.take(timeout)
        except QueueTimeout as e:
            print(e, file=sys.stderr, flush=True)
            return TIMEOUT_EXIT
        except Cancelled:
            return 128 + signals[0]
        if signals:
            return 128 + signals[0]
        if turn.tries > 1 and turn.expired():
            print(f"excel-queue: no turn within {timeout:g}s; the command was not started", file=sys.stderr,
                  flush=True)
            return TIMEOUT_EXIT
        try:
            child = subprocess.Popen(command, **GROUP)
        except OSError as e:
            print(ascii_text(f"excel-queue: could not start {command[0]}: {e}"), file=sys.stderr, flush=True)
            return 127
        while not signals and child.poll() is None:
            time.sleep(0.05)
        if not signals:
            return 128 - child.returncode if child.returncode < 0 else child.returncode
        stop_group(child)
        blocker = excel_blocker()
        if blocker:
            turn.abandon(f"stopped by signal {signals[0]} while {blocker}")
        return 128 + signals[0]
    finally:
        turn.release()
        restore()


def release_stale(root: Path | None = None) -> int:
    root = root or queue_dir()
    seen: dict = {}
    who = whoami()
    watch = RecoveryWatch(root)
    while True:
        watch.poll()
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
        if confirmed_stale(holder, seen):
            try:
                if break_lock(root, holder, who, watch):
                    return 0
            except RecoveryRefused as e:
                note(root, refusal(holder, str(e)))
                return 1
        time.sleep(POLL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=SCRIPT, description="The machine-wide Excel queue: one Excel "
                                     "automation at a time on this desktop.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="who holds Excel and who is waiting")
    turn = sub.add_parser("run", help="take a turn, run the command, give the turn back; exits with its code")
    turn.add_argument("--purpose", help="what the turn is for, shown to those waiting (default: the command)")
    turn.add_argument("--timeout", type=float, metavar="SECONDS",
                      help=f"give up after waiting this long and exit {TIMEOUT_EXIT} (default: wait indefinitely)")
    turn.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command to run")
    release = sub.add_parser("release", help="break the lock of a stale holder")
    release.add_argument("--stale-only", action="store_true", required=True,
                         help="required: only a stale holder's lock is broken, never a live one or one whose "
                              "automation Excel is still running")
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
