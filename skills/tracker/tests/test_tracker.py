"""Tracker behavior tests use isolated fixtures and hand-written expectations."""
import json
import os
import subprocess
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import tracker  # noqa: E402

BASE = '''
import os

def alpha(x):
    return x

def beta():
    return 2

def gamma(a):
    return a

class Foo:
    pass
'''

HEAD = '''
import os

def alpha(x):
    return x + 1

def beta():
    return 2

class Foo:
    pass

def delta():
    pass
'''


def test_function_changes_compares_each_function_source():
    got = tracker.function_changes(BASE, HEAD)
    # beta and Foo are present on both sides but unchanged: not edited.
    assert got == {"edited": ["alpha"], "removed": ["gamma"], "added": ["delta"]}, got


def test_function_changes_new_and_deleted_files():
    assert tracker.function_changes(None, HEAD)["added"] == ["Foo", "alpha", "beta", "delta"]
    assert tracker.function_changes(BASE, None)["removed"] == ["Foo", "alpha", "beta", "gamma"]
    assert tracker.function_changes("def broken(:\n", HEAD) is None


def _pr(head):
    return {"head_sha": head, "comments": [{"id": "c1", "at": "2026-09-28T18:19:39Z"},
                                           {"id": "c2", "at": "2026-09-28T19:00:00Z"}]}


LEDGER = {"comments": {"c1": {"pr": 81, "at": "2026-09-28T18:19:39Z", "kind": "review",
                              "verdict": "BLOCKED", "round": 2, "head": "cd33565",
                              "summary": "pairs builder collides with #91", "signers": []}}}


def test_recorded_review_is_current_only_at_its_head():
    same = tracker.reading(_pr("cd33565a0b1c2d3e4f"), LEDGER)
    moved = tracker.reading(_pr("6e1fee8aaaaaaaaaaa"), LEDGER)
    assert same["latest_review"]["verdict"] == "BLOCKED"
    assert same["latest_review"]["current"] is True
    assert moved["latest_review"]["current"] is False
    assert same["unread"] == ["c2"]


def test_review_record_needs_a_verdict():
    args = Namespace(kind="review", verdict=None, round=2, head="cd33565", checkout=Path("."),
                     repo="x/y", number=81, comment="c1", summary="s", net=None, asks_note_on=None)
    with patch.object(tracker, "GitHub", side_effect=RuntimeError("offline test")):
        try:
            tracker.record(args)
            outcome = "recorded"
        except tracker.Refused:
            return
        except Exception as exc:
            outcome = f"{type(exc).__name__}: {exc}"
    raise AssertionError(f"a review without --verdict was not refused ({outcome})")


def test_commit_signers_come_from_git_trailers():
    with tempfile.TemporaryDirectory() as tmp:
        checkout = Path(tmp)
        git_env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Tracker Test",
            "GIT_AUTHOR_EMAIL": "tracker@example.invalid",
            "GIT_COMMITTER_NAME": "Tracker Test",
            "GIT_COMMITTER_EMAIL": "tracker@example.invalid",
        }
        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(checkout), *args], env=git_env, text=True
            ).strip()

        git("init", "--quiet")
        git("commit", "--quiet", "--allow-empty", "-m",
            'Signed fixture\n\nClaude-Session: 664610fa "review review"')
        signed = git("rev-parse", "HEAD")[:7]
        git("commit", "--quiet", "--allow-empty", "-m",
            'Unsigned fixture mentions Claude-Session: 664610fa "review review" in prose')
        unsigned = git("rev-parse", "HEAD")[:7]
        found = tracker.commit_signers(checkout, [signed, unsigned, "0000000"])
        assert found[signed] == [{"id": "664610fa", "title": "review review"}], found
        assert found[unsigned] == [], found
        assert found["0000000"] is None, found


def _line(**entry):
    return json.dumps(entry) + "\n"


def test_signers_read_the_body_line():
    body = 'text\n\nClaude session "review review" \u00b7 664610fa\nClaude session "x" 12345678'
    assert tracker.Identity.signers(body) == [("664610fa", "review review")]


def test_pr_touches_count_links_and_urls_of_this_repo_only():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "s.jsonl"
        f.write_text(
            _line(type="pr-link", prNumber=12, prRepository="o/r", timestamp="2026-09-28T10:00:00Z",
                  prUrl="https://github.com/o/r/pull/12") +
            _line(type="user", timestamp="2026-09-28T11:00:00Z",
                  message="see https://github.com/o/r/pull/12 and https://github.com/o/r/pull/123") +
            _line(type="user", timestamp="2026-09-28T12:00:00Z", message="https://github.com/o/other/pull/12") +
            _line(type="pr-link", prNumber=12, prRepository="o/other", timestamp="2026-09-28T13:00:00Z",
                  prUrl="https://github.com/o/other/pull/12"),
            encoding="utf-8")
        _, hits = tracker._pr_touch_file((str(f), "o/r", [12]))
    assert hits == {12: {"first": "2026-09-28T10:00:00Z", "last": "2026-09-28T11:00:00Z",
                         "mentions": 1, "linked": 1}}, hits


def test_worktree_sessions_read_structured_fields_only():
    root = "/x/ep-foo"
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "projects" / "p"
        proj.mkdir(parents=True)
        a = "aaaaaaaa-0000-0000-0000-000000000001"
        b = "bbbbbbbb-0000-0000-0000-000000000002"
        (proj / f"{a}.jsonl").write_text(
            _line(type="assistant", timestamp="2026-09-28T10:00:00Z", cwd="/home/u",
                  message={"content": [{"type": "tool_use", "name": "Edit",
                                        "input": {"file_path": root + "/scripts/a.py"}}]}) +
            # a command naming the worktree is text, not a structured field: not counted
            _line(type="assistant", timestamp="2026-09-28T10:01:00Z", cwd="/home/u",
                  message={"content": [{"type": "tool_use", "name": "Bash",
                                        "input": {"command": "git -C /x/ep-foo status"}}]}) +
            # a sibling folder whose name starts the same is not the worktree
            _line(type="user", timestamp="2026-09-28T10:02:00Z", cwd="/x/ep-foo-other"),
            encoding="utf-8")
        (proj / f"{b}.jsonl").write_text(
            _line(type="user", timestamp="2026-09-28T11:00:00Z", cwd=root), encoding="utf-8")
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = tmp
        try:
            got = tracker.worktree_sessions(tracker.Identity, [root])
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
    rows = {r["id"]: (r["edited"], r["cwd"]) for r in got[root]}
    assert rows == {a: (1, 0), b: (0, 1)}, rows


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}\n{exc}")
    sys.exit(1 if failed else 0)
