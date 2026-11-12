"""The machine-wide Excel queue (scripts/excel_queue.py): one holder at a time, served in arrival order.

Stdlib only: python3 skills/excel-queue/tests/test_excel_queue.py

Each session is a thread on a queue folder in a temporary directory (EXCEL_QUEUE_DIR), polling every few
milliseconds; only a nested turn in the same thread joins its turn, so threads stand for separate sessions. A Desk
counts how many sessions hold Excel at once and the order they got it in, so a lock two sessions both win, or a
turn out of order, fails a test. A crashed or skewed session is played by the file it leaves: a holder.json or a
ticket whose heartbeat stopped, or keeps changing on a clock that runs behind, written out by hand. The script runs
as another session runs it, `python3 excel_queue.py run -- <command>`, with a child Python as the command; no
other process is started and no Excel is touched.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "excel_queue.py"
sys.path.insert(0, str(SCRIPT.parent))

import excel_queue as Q  # noqa: E402

RECORD_TIME = "%Y-%m-%dT%H:%M:%S.%fZ"
NAME_TIME = "%Y%m%dT%H%M%S.%fZ"


def eventually(condition, seconds: float = 10.0) -> None:
    end = time.monotonic() + seconds
    while not condition():
        if time.monotonic() >= end:
            raise AssertionError("the condition never held")
        time.sleep(0.005)


def in_line() -> list[str]:
    return [t["purpose"] for t in Q.status()["queue"]]


def ago(seconds: float, form: str = RECORD_TIME) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(form)


def ghost(ticket: str, purpose: str, heartbeat_ago: float, held_minutes: float = 0) -> str:
    """The record another session wrote, its last heartbeat `heartbeat_ago` seconds back, started `held_minutes`
    before that."""
    beat, started = ago(heartbeat_ago), ago(heartbeat_ago + 60 * held_minutes)
    return json.dumps({"who": "ghost (WSL Ubuntu)", "host": "DESKTOP", "pid": 4242, "purpose": purpose,
                       "arrived": started, "started": started, "heartbeat": beat, "ticket": ticket})


def ghost_holder(root: Path, purpose: str, heartbeat_ago: float) -> Path:
    lock = root / "lock"
    lock.mkdir(parents=True)
    (lock / "holder.json").write_text(ghost("ghost-ticket", purpose, heartbeat_ago))
    return lock


def beating(path: Path, record: str) -> tuple[threading.Event, threading.Thread]:
    """A live writer on a clock that runs behind: it writes `record` (a heartbeat that always reads old) to `path`
    now, then rewrites it every 5 ms, so the heartbeat keeps changing, until the event is set."""
    path.write_text(record)
    stop = threading.Event()

    def beat():
        while not stop.is_set():
            staged = path.parent / f".{path.name}.beat"
            staged.write_text(json.dumps({**json.loads(record), "heartbeat": ago(660)}))
            os.replace(staged, path)
            time.sleep(0.005)
    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    return stop, thread


def child(code: int) -> list[str]:
    """A child Python that exits `code` when the queue's holder is the turn of purpose 'probe', 99 otherwise."""
    check = ("import json, os, sys; "
             "path = os.path.join(os.environ['EXCEL_QUEUE_DIR'], 'lock', 'holder.json'); "
             f"sys.exit({code} if json.load(open(path))['purpose'] == 'probe' else 99)")
    return [sys.executable, "-c", check]


class Desk:
    """Sessions in threads. Each holds Excel until its event is set, then a moment longer, so two holding at once
    would overlap; `most` is the most that ever held it at once, `order` the order they got it in."""

    def __init__(self):
        self.guard = threading.Lock()
        self.holding = 0
        self.most = 0
        self.order: list[str] = []
        self.errors: list[BaseException] = []
        self.threads: list[threading.Thread] = []

    def session(self, purpose: str, done: threading.Event) -> None:
        def work():
            try:
                with Q.excel_turn(purpose):
                    with self.guard:
                        self.holding += 1
                        self.most = max(self.most, self.holding)
                        self.order.append(purpose)
                    done.wait(10)
                    time.sleep(0.05)
                    with self.guard:
                        self.holding -= 1
            except BaseException as e:
                self.errors.append(e)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.threads.append(thread)

    def finish(self) -> None:
        for thread in self.threads:
            thread.join(10)
            assert not thread.is_alive()


class QueueTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.queue = self.tmp / "excel-queue"
        env = mock.patch.dict(os.environ, {"EXCEL_QUEUE_DIR": str(self.queue)})
        env.start()
        self.addCleanup(env.stop)
        self.tune(POLL_SECONDS=0.005, HEARTBEAT_SECONDS=0.02, CONFIRM_POLLS=3)

    def tune(self, **constants):
        for name, value in constants.items():
            patch = mock.patch.object(Q, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def cleared(self) -> bool:
        return not (self.queue / "lock").exists() and list((self.queue / "tickets").iterdir()) == []

    def test_a_second_session_waits_until_the_first_gives_excel_back(self):
        desk, first, second = Desk(), threading.Event(), threading.Event()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            desk.session("first", first)
            eventually(lambda: desk.order == ["first"])
            desk.session("second", second)
            eventually(lambda: in_line() == ["second"])
            time.sleep(0.2)
            self.assertEqual(desk.order, ["first"])
            first.set()
            eventually(lambda: desk.order == ["first", "second"])
            second.set()
            desk.finish()
        self.assertEqual((desk.most, desk.errors, self.cleared()), (1, [], True))
        waiting = [line for line in err.getvalue().splitlines() if line.startswith("excel-queue: waiting")]
        self.assertEqual(len(waiting), 1)
        self.assertTrue(waiting[0].isascii())
        self.assertRegex(waiting[0], r"^excel-queue: waiting for Excel, held by .+ on \S+ \(pid \d+\) since "
                                     r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\dZ for: first; place in line 1 of 1$")

    def test_waiting_sessions_are_served_in_the_order_they_arrived(self):
        desk, holder, served = Desk(), threading.Event(), threading.Event()
        served.set()
        with contextlib.redirect_stderr(io.StringIO()):
            desk.session("holder", holder)
            eventually(lambda: desk.order == ["holder"])
            for arrived, purpose in enumerate(("w1", "w2", "w3"), 1):
                desk.session(purpose, served)
                eventually(lambda: len(in_line()) == arrived)
            self.assertEqual(in_line(), ["w1", "w2", "w3"])
            holder.set()
            desk.finish()
        self.assertEqual((desk.order, desk.most, desk.errors), (["holder", "w1", "w2", "w3"], 1, []))

    def test_a_nested_turn_in_one_thread_joins_the_turn_it_is_in(self):
        turns = []

        def nested():
            with Q.excel_turn("outer") as outer:
                with Q.excel_turn("inner", timeout=5) as inner:
                    turns.append((outer.name, inner.name, Q.status()["holder"]["purpose"], len(Q.status()["queue"])))
        session = threading.Thread(target=nested, daemon=True)
        session.start()
        session.join(10)
        self.assertFalse(session.is_alive())
        (outer, inner, purpose, waiting), = turns
        self.assertEqual((inner, purpose, waiting, self.cleared()), (outer, "outer", 0, True))

    def test_an_earlier_live_ticket_goes_first_and_a_stale_one_is_skipped(self):
        tickets = self.queue / "tickets"
        tickets.mkdir(parents=True)
        name = f"{ago(700, NAME_TIME)}-{'a' * 32}"
        earlier = tickets / f"{name}.json"
        earlier.write_text(ghost(name, "earlier arrival", heartbeat_ago=60))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(Q.QueueTimeout, "free, with earlier arrivals ahead; place in line 2 of 2$"):
                with Q.excel_turn("later arrival", timeout=0.3):
                    self.fail("took Excel ahead of an earlier arrival that is still waiting")
            self.assertFalse((self.queue / "lock").exists())
            self.assertEqual([p.name for p in tickets.iterdir()], [earlier.name])

            earlier.write_text(ghost(name, "earlier arrival", heartbeat_ago=601))
            with Q.excel_turn("later arrival", timeout=5):
                self.assertEqual(Q.status()["holder"]["purpose"], "later arrival")
                self.assertFalse(earlier.exists())
        self.assertRegex((self.queue / "queue.log").read_text(),
                         r"removed the stale ticket of ghost \(WSL Ubuntu\) on DESKTOP \(pid 4242\) \(heartbeat "
                         r"60\ds old\), for: earlier arrival")

    def test_a_stale_holder_is_broken_and_its_lock_kept(self):
        lock = ghost_holder(self.queue, "crashed run", heartbeat_ago=601)
        with contextlib.redirect_stderr(io.StringIO()):
            with Q.excel_turn("next run", timeout=5):
                self.assertEqual(Q.status()["holder"]["purpose"], "next run")
                (kept,) = self.queue.glob("stale-*")
                self.assertEqual(json.loads((kept / "holder.json").read_text())["purpose"], "crashed run")
        self.assertEqual((lock.exists(), Q.status()["stale_locks"]), (False, [kept.name]))
        self.assertRegex((self.queue / "queue.log").read_text().strip(),
                         rf"broke the stale lock of ghost \(WSL Ubuntu\) on DESKTOP \(pid 4242\) \(heartbeat 60\ds "
                         rf"old\), for: crashed run; kept as {kept.name}$")

    def test_a_live_holder_is_never_broken(self):
        lock = ghost_holder(self.queue, "live run", heartbeat_ago=540)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(Q.QueueTimeout, r"held by ghost \(WSL Ubuntu\) on DESKTOP \(pid 4242\) "
                                                        r"since .* for: live run; place in line 1 of 1$"):
                with Q.excel_turn("next run", timeout=0.3):
                    self.fail("broke a live holder's lock")
        self.assertEqual(json.loads((lock / "holder.json").read_text())["purpose"], "live run")
        self.assertEqual(list(self.queue.glob("stale-*")), [])

    def test_a_live_holder_whose_clock_runs_behind_is_never_broken(self):
        """Its heartbeat always reads 11 minutes old, but it keeps changing, as a live holder's does."""
        self.tune(CONFIRM_POLLS=40)
        lock = self.queue / "lock"
        lock.mkdir(parents=True)
        stop, thread = beating(lock / "holder.json", ghost("ghost-ticket", "slow clock", heartbeat_ago=660))
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(Q.QueueTimeout):
                    with Q.excel_turn("next run", timeout=0.6):
                        self.fail("broke a live holder's lock")
        finally:
            stop.set()
            thread.join(10)
        self.assertEqual(list(self.queue.glob("stale-*")), [])

    def test_a_holder_that_outlasts_the_stale_limit_keeps_its_turn(self):
        self.tune(STALE_SECONDS=0.3, CONFIRM_POLLS=10)
        desk, first, second = Desk(), threading.Event(), threading.Event()
        second.set()
        with contextlib.redirect_stderr(io.StringIO()):
            desk.session("long run", first)
            eventually(lambda: desk.order == ["long run"])
            desk.session("next", second)
            eventually(lambda: in_line() == ["next"])
            time.sleep(1.0)
            self.assertEqual(desk.order, ["long run"])
            first.set()
            desk.finish()
        self.assertEqual((desk.order, desk.most, desk.errors, list(self.queue.glob("stale-*"))),
                         (["long run", "next"], 1, [], []))

    def test_a_waiter_whose_clock_runs_behind_keeps_its_place_until_it_stops(self):
        """Its ticket's heartbeat always reads 11 minutes old, but keeps changing while it lives, as a live waiter's
        does on a clock 11 minutes behind; it arrived first, so it stays ahead until it really stops."""
        self.tune(CONFIRM_POLLS=40)
        tickets = self.queue / "tickets"
        tickets.mkdir(parents=True)
        name = f"{ago(660, NAME_TIME)}-{'b' * 32}"
        slow = tickets / f"{name}.json"
        stop, thread = beating(slow, ghost(name, "slow clock", heartbeat_ago=660))
        desk, served = Desk(), threading.Event()
        served.set()
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                desk.session("later arrival", served)
                eventually(lambda: len(in_line()) == 2)
                time.sleep(0.5)
                self.assertEqual((desk.order, slow.exists(), (self.queue / "queue.log").exists()), ([], True, False))
            finally:
                stop.set()
                thread.join(10)
            desk.finish()
        removed = [line for line in (self.queue / "queue.log").read_text().splitlines()
                   if "removed the stale ticket" in line]
        self.assertEqual((desk.order, desk.errors, slow.exists(), len(removed)), (["later arrival"], [], False, 1))
        self.assertTrue(removed[0].endswith("for: slow clock"))

    def test_break_lock_leaves_a_lock_taken_since_it_was_confirmed(self):
        lock = ghost_holder(self.queue, "crashed run", heartbeat_ago=601)
        confirmed = Q.read_holder(self.queue)
        (lock / "holder.json").write_text(ghost("a later holder's ticket", "later run", heartbeat_ago=601))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIs(Q.break_lock(self.queue, confirmed, "tester"), False)
            self.assertEqual(json.loads((lock / "holder.json").read_text())["ticket"], "a later holder's ticket")
            self.assertEqual(list(self.queue.glob("stale-*")), [])
            self.assertIs(Q.break_lock(self.queue, Q.read_holder(self.queue), "tester"), True)
        (kept,) = self.queue.glob("stale-*")
        self.assertEqual((lock.exists(), json.loads((kept / "holder.json").read_text())["purpose"]),
                         (False, "later run"))

    def test_release_stale_only_breaks_only_a_stale_lock(self):
        for stale in (True, False):
            with self.subTest(stale=stale):
                lock = ghost_holder(self.queue, "crashed run", heartbeat_ago=601 if stale else 540)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = Q.main(["release", "--stale-only"])
                self.assertEqual((code, lock.exists(), len(list(self.queue.glob("stale-*")))),
                                 (0, False, 1) if stale else (1, True, 1))

    def test_status_lists_the_holder_and_the_line(self):
        desk, held, waits = Desk(), threading.Event(), threading.Event()
        out = io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()):
            desk.session("a long recalc", held)
            eventually(lambda: desk.order == ["a long recalc"])
            desk.session("another session's probe", waits)
            eventually(lambda: len(in_line()) == 1)
            with contextlib.redirect_stdout(out):
                self.assertEqual(Q.main(["status"]), 0)
            held.set()
            waits.set()
            desk.finish()
        lines = out.getvalue().splitlines()
        self.assertRegex(lines[1], r"^holder: .* since .*, heartbeat \d+s ago: a long recalc \(held 0 min\)$")
        self.assertEqual(lines[2], "waiting: 1")
        self.assertRegex(lines[3], r"^  1\. .* since .*, heartbeat \d+s ago: another session's probe$")

    def test_status_shows_how_long_the_holder_has_held_excel(self):
        ghost_holder(self.queue, "hung run", heartbeat_ago=5)
        (self.queue / "lock" / "holder.json").write_text(
            ghost("ghost-ticket", "hung run", heartbeat_ago=5, held_minutes=47))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(Q.main(["status"]), 0)
        self.assertRegex(out.getvalue().splitlines()[1], r"^holder: ghost \(WSL Ubuntu\) on DESKTOP \(pid 4242\) "
                                                         r"since .*, heartbeat \ds ago: hung run \(held 47 min\)$")

    def test_run_holds_the_turn_for_the_command_and_passes_its_exit_code(self):
        for code in (0, 7):
            with self.subTest(code=code):
                done = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "run", "--purpose", "probe", "--",
                                       *child(code)], cwd=self.tmp, capture_output=True, text=True, timeout=60)
                self.assertEqual(done.returncode, code, done.stderr)
                self.assertTrue(self.cleared())

    def test_run_never_starts_the_command_while_another_session_holds_excel(self):
        marker = self.tmp / "ran"
        with Q.excel_turn("a long recalc"):
            done = subprocess.run([sys.executable, str(SCRIPT), "run", "--timeout", "0.5", "--", sys.executable,
                                   "-c", f"open({str(marker)!r}, 'w')"], capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, marker.exists()), (Q.TIMEOUT_EXIT, False))
        waiting, gave_up = done.stderr.splitlines()
        self.assertRegex(waiting, r"^excel-queue: waiting for Excel, held by .* for: a long recalc; place in line "
                                  r"1 of 1$")
        self.assertTrue(gave_up.startswith("excel-queue: no turn within 0.5s, held by "))
        self.assertTrue(self.cleared())


if __name__ == "__main__":
    unittest.main()
