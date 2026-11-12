from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import signal
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
LOADING = importlib.util.spec_from_file_location("excel_queue", SCRIPT)
Q = importlib.util.module_from_spec(LOADING)
LOADING.loader.exec_module(Q)

QUERY = Q.live_automation_excels
RECORD_TIME = "%Y-%m-%dT%H:%M:%S.%fZ"
NAME_TIME = "%Y%m%dT%H%M%S.%fZ"
GRANDCHILD = """
import os, signal, sys, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
if sys.argv[2] == "slow":
    signal.signal(signal.SIGTERM, lambda *_: (time.sleep(0.3), os._exit(0)))
else:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
open(sys.argv[1] + ".grand.tmp", "w").write(str(os.getpid()))
os.replace(sys.argv[1] + ".grand.tmp", sys.argv[1] + ".grand")
while True:
    time.sleep(0.05)
"""
JOB = f"""
import os, subprocess, sys, time
subprocess.Popen([sys.executable, "-c", {GRANDCHILD!r}, *sys.argv[1:]])
open(sys.argv[1] + ".child", "w").write(str(os.getpid()))
time.sleep(60)
"""


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
    beat, started = ago(heartbeat_ago), ago(heartbeat_ago + 60 * held_minutes)
    return json.dumps({"who": "ghost (WSL Ubuntu)", "host": "DESKTOP", "pid": 4242, "purpose": purpose,
                       "arrived": started, "started": started, "heartbeat": beat, "ticket": ticket})


def ghost_holder(root: Path, purpose: str, heartbeat_ago: float) -> Path:
    lock = root / "lock"
    lock.mkdir(parents=True)
    (lock / "holder.json").write_text(ghost("ghost-ticket", purpose, heartbeat_ago))
    return lock


def beating(path: Path, record: str) -> tuple[threading.Event, threading.Thread]:
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
    check = ("import json, os, sys; "
             "path = os.path.join(os.environ['EXCEL_QUEUE_DIR'], 'lock', 'holder.json'); "
             f"sys.exit({code} if json.load(open(path))['purpose'] == 'probe' else 99)")
    return [sys.executable, "-c", check]


def wrapper(*args: str, excels: str = "[]", grace: float = 30.0) -> list[str]:
    launch = (f"import sys; sys.path.insert(0, {str(SCRIPT.parent)!r}); import excel_queue as Q; "
              f"Q.live_automation_excels = lambda: {excels}; Q.GRACE_SECONDS = {grace}; "
              f"sys.exit(Q.main(sys.argv[1:]))")
    return [sys.executable, "-c", launch, *args]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class Desk:
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
        self.tune(POLL_SECONDS=0.005, HEARTBEAT_SECONDS=0.02, CONFIRM_POLLS=3, EXCEL_RECHECK_SECONDS=0.0,
                  live_automation_excels=lambda: [])

    def tune(self, **constants):
        for name, value in constants.items():
            patch = mock.patch.object(Q, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def cleared(self) -> bool:
        return not (self.queue / "lock").exists() and list((self.queue / "tickets").iterdir()) == []

    def test_a_waiter_that_waits_past_the_stale_limit_keeps_its_ticket_fresh(self):
        self.tune(STALE_SECONDS=0.3, CONFIRM_POLLS=10)
        desk, holder, served = Desk(), threading.Event(), threading.Event()
        served.set()
        ages: list[float] = []
        with contextlib.redirect_stderr(io.StringIO()):
            desk.session("holder", holder)
            eventually(lambda: desk.order == ["holder"])
            desk.session("long wait", served)
            eventually(lambda: in_line() == ["long wait"])
            end = time.monotonic() + 1.0
            while time.monotonic() < end:
                ages += [t["age"] for t in Q.status()["queue"] if t.get("purpose") == "long wait"]
                time.sleep(0.01)
            holder.set()
            desk.finish()
        self.assertTrue(ages)
        self.assertLess(max(ages), 0.3)
        self.assertEqual((desk.order, desk.errors, (self.queue / "queue.log").exists()),
                         (["holder", "long wait"], [], False))

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
            self.assertIs(Q.break_lock(self.queue, confirmed, "tester", Q.RecoveryWatch(self.queue)), False)
            self.assertEqual(json.loads((lock / "holder.json").read_text())["ticket"], "a later holder's ticket")
            self.assertEqual(list(self.queue.glob("stale-*")), [])
            self.assertIs(Q.break_lock(self.queue, Q.read_holder(self.queue), "tester", Q.RecoveryWatch(self.queue)),
                          True)
        (kept,) = self.queue.glob("stale-*")
        self.assertEqual((lock.exists(), json.loads((kept / "holder.json").read_text())["purpose"]),
                         (False, "later run"))

    def test_release_stale_only_breaks_only_a_stale_lock(self):
        for stale in (True, False):
            with self.subTest(stale=stale):
                root = self.tmp / f"case-{stale}"
                with mock.patch.dict(os.environ, {"EXCEL_QUEUE_DIR": str(root)}):
                    lock = ghost_holder(root, "crashed run", heartbeat_ago=601 if stale else 540)
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        code = Q.main(["release", "--stale-only"])
                self.assertEqual((code, lock.exists(), len(list(root.glob("stale-*")))),
                                 (0, False, 1) if stale else (1, True, 0))

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

    def test_run_with_timeout_zero_takes_a_free_turn_at_once(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "run", "--timeout", "0", "--purpose", "probe", "--",
                               *child(5)], capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, self.cleared()), (5, True), done.stderr)

    def test_a_turn_freed_after_the_deadline_is_not_taken(self):
        marker = self.tmp / "ran"
        holder = Q.Turn("a long recalc")
        holder.take()
        try:
            waiter = subprocess.Popen([sys.executable, str(SCRIPT), "run", "--timeout", "0.3", "--", sys.executable,
                                       "-c", f"open({str(marker)!r}, 'w')"], stderr=subprocess.PIPE, text=True)
            eventually(lambda: len(in_line()) == 1)
            time.sleep(0.5)
        finally:
            holder.release()
        _, err = waiter.communicate(timeout=60)
        self.assertEqual((waiter.returncode, marker.exists()), (Q.TIMEOUT_EXIT, False), err)
        self.assertTrue(self.cleared())

    def test_take_gives_up_when_the_turn_frees_after_the_deadline(self):
        self.tune(POLL_SECONDS=0.5)
        holder, waiter = Q.Turn("a long recalc"), Q.Turn("late")
        holder.take()
        freed = threading.Timer(0.25, holder.release)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                freed.start()
                with self.assertRaises(Q.QueueTimeout):
                    waiter.take(0.1)
        finally:
            waiter.release()
            freed.join()
        self.assertTrue(self.cleared())

    def shutdown(self, sig: int, grandchild: str, excels: str = "[]", grace: float = 30.0):
        pids = self.tmp / f"job-{sig}-{grandchild}"
        errors = self.tmp / f"{pids.name}.err"
        with open(errors, "w") as err:
            run = subprocess.Popen(wrapper("run", "--purpose", "job", "--", sys.executable, "-c", JOB, str(pids),
                                           grandchild, excels=excels, grace=grace), stderr=err)
        job: list[int] = []
        try:
            eventually(lambda: Path(f"{pids}.child").exists() and Path(f"{pids}.grand").exists())
            job = [int(Path(f"{pids}.{part}").read_text()) for part in ("child", "grand")]
            self.assertTrue((self.queue / "lock").exists())
            samples, started = [], time.monotonic()
            os.kill(run.pid, sig)
            while run.poll() is None and time.monotonic() - started < 30:
                samples.append(((self.queue / "lock").exists(), any(alive(pid) for pid in job)))
                time.sleep(0.002)
            elapsed = time.monotonic() - started
            self.assertEqual(run.wait(5), 128 + sig, errors.read_text())
        finally:
            for pid in job:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
            if run.poll() is None:
                run.kill()
                run.wait()
        self.assertFalse(any(alive(pid) for pid in job))
        self.assertTrue(all(locked for locked, running in samples if running))
        self.assertTrue(any(running for _, running in samples))
        return samples, elapsed

    def test_a_signalled_run_stops_its_whole_job_before_it_gives_the_turn_back(self):
        for sig, grandchild, grace in ((signal.SIGTERM, "slow", 30.0), (signal.SIGINT, "slow", 30.0),
                                       (signal.SIGTERM, "stubborn", 0.5)):
            with self.subTest(signal=sig, grandchild=grandchild):
                samples, elapsed = self.shutdown(sig, grandchild, grace=grace)
                self.assertTrue(self.cleared())
                self.assertGreaterEqual(elapsed, 0.3 if grandchild == "slow" else 0.5)

    def test_a_signalled_run_leaves_an_abandoned_lock_while_automation_excel_lives(self):
        self.shutdown(signal.SIGTERM, "slow", excels="[4242]")
        holder = json.loads((self.queue / "lock" / "holder.json").read_text())
        self.assertEqual(holder["abandoned"], "stopped by signal 15 while automation Excel pids 4242 are running")
        self.assertEqual(list((self.queue / "tickets").iterdir()), [])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            Q.main(["status"])
        self.assertIn("  abandoned: stopped by signal 15 while automation Excel pids 4242", out.getvalue())

    def test_a_running_automation_excel_or_a_failed_check_blocks_every_stale_break(self):
        failed = Q.ExcelQueryError("the Excel check could not run: no powershell.exe")
        for n, (query, said) in enumerate(((mock.Mock(return_value=[4242, 4343]),
                                            "automation Excel pids 4242, 4343 are running"),
                                           (mock.Mock(side_effect=failed), str(failed)))):
            for how in ("waiter", "release --stale-only"):
                with self.subTest(said=said, how=how):
                    root = self.tmp / f"case-{n}-{how[:6]}"
                    err, out = io.StringIO(), io.StringIO()
                    with mock.patch.dict(os.environ, {"EXCEL_QUEUE_DIR": str(root)}), \
                            mock.patch.object(Q, "live_automation_excels", query), \
                            contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                        lock = ghost_holder(root, "crashed run", heartbeat_ago=601)
                        if how == "waiter":
                            with self.assertRaises(Q.QueueTimeout):
                                with Q.excel_turn("next run", timeout=0.3):
                                    self.fail("broke a lock while its Excel was running")
                        else:
                            self.assertEqual(Q.main(["release", "--stale-only"]), 1)
                        Q.main(["status"])
                    refused = [line for line in err.getvalue().splitlines() if "is stale" in line]
                    self.assertEqual(len(refused), 1)
                    self.assertTrue(refused[0].endswith(f"but {said}; end that Excel (Task Manager) or wait"))
                    self.assertIn(f"but {said}; end that Excel (Task Manager) or wait", out.getvalue())
                    self.assertEqual((lock.exists(), list(root.glob("stale-*")), (root / "recovery").exists()),
                                     (True, [], False))

    def test_the_excel_check_counts_only_running_automation_excels(self):
        found = [{"ProcessId": 11, "CommandLine": '"C:\\Office\\EXCEL.EXE" /automation -Embedding'},
                 {"ProcessId": 12, "CommandLine": '"C:\\Office\\EXCEL.EXE" "C:\\Users\\me\\book.xlsx"'},
                 {"ProcessId": 13, "CommandLine": None}]
        wsl = mock.Mock(release="5.15.167.4-microsoft-standard-WSL2")
        for stdout, code, expected in ((json.dumps(found), 0, [11]), (json.dumps(found[0]), 0, [11]),
                                       ("", 0, []), ("", 1, None), ("Excel: none", 0, None)):
            with self.subTest(stdout=stdout, code=code):
                done = subprocess.CompletedProcess([], code, stdout.encode(), b"")
                with mock.patch.object(Q.platform, "uname", return_value=wsl), \
                        mock.patch.object(Q.subprocess, "run", return_value=done) as ran:
                    if expected is None:
                        self.assertRaises(Q.ExcelQueryError, QUERY)
                    else:
                        self.assertEqual(QUERY(), expected)
                self.assertEqual(ran.call_args[0][0][:3], ["powershell.exe", "-NoProfile", "-NonInteractive"])
        with mock.patch.object(Q.platform, "uname", return_value=wsl), \
                mock.patch.object(Q.subprocess, "run", side_effect=FileNotFoundError("powershell.exe")):
            self.assertRaises(Q.ExcelQueryError, QUERY)
        with mock.patch.object(Q.platform, "uname", return_value=mock.Mock(release="6.1.0-generic")), \
                mock.patch.object(Q.subprocess, "run") as ran:
            self.assertRaisesRegex(Q.ExcelQueryError, "only Windows and WSL", QUERY)
        ran.assert_not_called()

    def test_two_breakers_cannot_displace_a_fresh_holder(self):
        ghost_holder(self.queue, "crashed run", heartbeat_ago=601)
        confirmed = Q.read_holder(self.queue)
        paused, resume, took = threading.Event(), threading.Event(), threading.Event()
        calls: list[str] = []

        def query():
            calls.append(threading.current_thread().name)
            if len(calls) == 1:
                paused.set()
                resume.wait(10)
            return []
        results: dict[str, bool] = {}
        fresh = Q.Turn("fresh holder", self.queue)

        def breaker(name):
            results[name] = Q.break_lock(self.queue, confirmed, name, Q.RecoveryWatch(self.queue))

        def take():
            end = time.monotonic() + 10
            while time.monotonic() < end and not fresh._lock():
                time.sleep(0.001)
            took.set()
        first = threading.Thread(target=breaker, args=("first",), name="first")
        second = threading.Thread(target=breaker, args=("second",), name="second")
        taker = threading.Thread(target=take)
        with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(Q, "live_automation_excels", query):
            first.start()
            self.assertTrue(paused.wait(10))
            taker.start()
            second.start()
            second.join(10)
            took.wait(0.5)
            resume.set()
            for thread in (first, taker):
                thread.join(10)
            self.assertIs(Q.break_lock(self.queue, confirmed, "third", Q.RecoveryWatch(self.queue)), False)
        try:
            self.assertEqual((results, calls), ({"first": True, "second": False}, ["first"]))
            self.assertEqual(json.loads((self.queue / "lock" / "holder.json").read_text())["ticket"], fresh.name)
            (kept,) = self.queue.glob("stale-*")
            self.assertEqual(json.loads((kept / "holder.json").read_text())["purpose"], "crashed run")
            self.assertFalse((self.queue / "recovery").exists())
        finally:
            fresh.release()

    def test_a_recovery_step_is_broken_only_once_seen_unchanged_for_the_confirmation_polls(self):
        for state, since in (("live", 3600), ("dead", 1)):
            with self.subTest(state=state):
                root = self.tmp / f"case-{state}"
                with mock.patch.dict(os.environ, {"EXCEL_QUEUE_DIR": str(root)}), \
                        contextlib.redirect_stderr(io.StringIO()):
                    lock = ghost_holder(root, "crashed run", heartbeat_ago=601)
                    step = root / "recovery"
                    step.mkdir()
                    record = json.dumps({"who": f"{state} breaker", "since": ago(since)})
                    if state == "live":
                        stop, thread = beating(step / "breaker.json", record)
                        try:
                            with self.assertRaises(Q.QueueTimeout):
                                with Q.excel_turn("next run", timeout=0.3):
                                    self.fail("broke a lock while another breaker was mid-step")
                        finally:
                            stop.set()
                            thread.join(10)
                    else:
                        (step / "breaker.json").write_text(record)
                        with Q.excel_turn("next run", timeout=5):
                            self.assertEqual(Q.status()["holder"]["purpose"], "next run")
                dead = state == "dead"
                self.assertEqual((lock.exists(), step.exists(), len(list(root.glob("recovery-dead-*")))),
                                 (not dead, not dead, int(dead)))
                if dead:
                    self.assertIn("broke the recovery step of dead breaker on ? (pid ?)",
                                  (root / "queue.log").read_text())

    def test_a_breaker_whose_clock_runs_behind_keeps_its_recovery_step(self):
        ghost_holder(self.queue, "crashed run", heartbeat_ago=601)
        confirmed = Q.read_holder(self.queue)
        paused, resume = threading.Event(), threading.Event()
        real_now = Q.utc_now

        def now():
            if threading.current_thread().name != "behind":
                return real_now()
            return (datetime.now(timezone.utc) - timedelta(seconds=70)).strftime(RECORD_TIME)

        def slow_check():
            if threading.current_thread().name == "behind":
                paused.set()
                resume.wait(10)
            return []
        results: dict = {"other": []}
        fresh = Q.Turn("fresh holder", self.queue)

        def behind():
            results["behind"] = Q.break_lock(self.queue, confirmed, "behind", Q.RecoveryWatch(self.queue))
        breaker = threading.Thread(target=behind, name="behind")
        with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(Q, "utc_now", now), \
                mock.patch.object(Q, "live_automation_excels", slow_check):
            breaker.start()
            self.assertTrue(paused.wait(10))
            watch = Q.RecoveryWatch(self.queue)
            for _ in range(Q.CONFIRM_POLLS - 1):
                watch.poll()
                results["other"].append(Q.break_lock(self.queue, confirmed, "other", watch))
                time.sleep(Q.POLL_SECONDS)
            took_early = fresh._lock()
            resume.set()
            breaker.join(10)
            took = took_early or fresh._lock()
        try:
            self.assertEqual((results, took_early, took),
                             ({"other": [False] * (Q.CONFIRM_POLLS - 1), "behind": True}, False, True))
            self.assertEqual(json.loads((self.queue / "lock" / "holder.json").read_text())["ticket"], fresh.name)
            (kept,) = self.queue.glob("stale-*")
            self.assertEqual(json.loads((kept / "holder.json").read_text())["purpose"], "crashed run")
            self.assertEqual(list(self.queue.glob("recovery*")), [])
        finally:
            fresh.release()

    def test_a_lock_replaced_during_the_excel_check_is_left_alone(self):
        lock = ghost_holder(self.queue, "crashed run", heartbeat_ago=601)
        confirmed = Q.read_holder(self.queue)
        fresh = Q.Turn("fresh holder", self.queue)

        def check():
            (lock / "holder.json").unlink()
            lock.rmdir()
            self.assertTrue(fresh._lock())
            return []
        with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(Q, "live_automation_excels", check):
            result = Q.break_lock(self.queue, confirmed, "breaker", Q.RecoveryWatch(self.queue))
        try:
            self.assertIs(result, False)
            self.assertEqual(json.loads((lock / "holder.json").read_text())["ticket"], fresh.name)
            self.assertEqual(list(self.queue.glob("stale-*")), [])
        finally:
            fresh.release()

    def test_an_unusable_queue_folder_fails_loudly(self):
        real = os.mkdir

        def mkdir(path, *args, **kwargs):
            if Path(path).name == "lock":
                raise PermissionError(13, "Permission denied")
            return real(path, *args, **kwargs)
        with mock.patch.object(Q.os, "mkdir", mkdir), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(OSError) as raised:
                with Q.excel_turn("next run", timeout=5):
                    self.fail("took a lock it could not make")
        self.assertNotIsInstance(raised.exception, Q.QueueTimeout)
        self.assertIn(f"cannot use the queue folder {self.queue}", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
