"""Tracker: read-only facts about a GitHub repo's pull requests, their stacks,
CI and merge state, the Claude Code sessions that touched them, and the local
worktrees that hold work GitHub cannot see.

The script gathers and computes; it never reads prose. Verdicts, rounds,
reviewed heads and asked-for notes are read from the comments by the model,
which records them with `record`. The script keeps that ledger, shows which
comments nobody has read yet, and compares a recorded head with the PR's
current head.

    python tracker.py sweep                    fetch everything, save a snapshot, print the digest and what changed
    python tracker.py pr 81                    one PR in depth, with the full text of its unread comments
    python tracker.py pr 81 --comments all     ... with every comment's full text
    python tracker.py record 81 --comment 123 --kind review --verdict BLOCKED --round 2 --head cd33565 \
        --summary "pairs builder collides with #91" [--net "worth it with a change"] [--asks-note-on 73]
    python tracker.py record 81 --comment 124 --kind note --summary "author's fix note for round 1"
    python tracker.py diff                     what changed between the last two snapshots

Common options: --repo owner/name (default: the checkout's origin), --checkout PATH (default: the
main worktree of the git repo in the current directory), --json.

Read-only toward GitHub (GET only) and git (`fetch` and read commands only).
Writes only under ~/.claude/tracker/: snapshots/ and ledger.json.

Exit codes: 0 done (per-item errors are listed in the output),
2 could not run (no GitHub token, GitHub unreachable, checkout missing),
3 `record` refused (no such comment on that PR, or --kind review without --verdict).
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _toplevel() -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return Path.cwd()
    common = Path(out.stdout.strip()) if out.returncode == 0 else None
    return common.parent if common and common.name == ".git" else Path.cwd()


def repo_of(checkout: Path) -> str:
    """owner/name from the checkout's origin remote."""
    try:
        url = subprocess.run(["git", "-C", str(checkout), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        url = ""
    found = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return found.group(1) if found else ""


DEFAULT_CHECKOUT = _toplevel()
CONFIG = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
HOME = CONFIG / "tracker"
SNAPSHOTS = HOME / "snapshots"
LEDGER = HOME / "ledger.json"
API = "https://api.github.com"
OWN_SESSION = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
VERDICTS = ("PASS", "BLOCKED", "UNCLEAR")
KINDS = ("review", "note", "decision", "fixes", "other")


class CouldNotRun(Exception):
    pass


class Refused(Exception):
    pass


class GitHubError(Exception):
    pass


# ---------------------------------------------------------------- GitHub

class GitHub:
    """GET-only client; the token comes from `gh auth token`, else git's credential store."""

    def __init__(self, repo: str):
        self.repo = repo
        self.token = self._token()

    @staticmethod
    def _token() -> str:
        for env in ("GH_TOKEN", "GITHUB_TOKEN"):
            if os.environ.get(env):
                return os.environ[env]
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            out = subprocess.run(["git", "credential", "fill"],
                                 input="protocol=https\nhost=github.com\n\n",
                                 capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise CouldNotRun(f"no GitHub token: gh auth token and git credential fill failed: {exc}")
        for line in out.splitlines():
            if line.startswith("password="):
                return line[len("password="):]
        raise CouldNotRun("no GitHub token from gh auth token or git credential fill")

    def get(self, path: str):
        url = path if path.startswith("http") else f"{API}/repos/{self.repo}/{path}"
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tracker"}
        error = ""
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as resp:
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                error = f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace').strip()}"
                if exc.code not in (502, 503, 504):
                    break
            except (urllib.error.URLError, OSError, ValueError) as exc:
                error = str(exc)
            time.sleep(2 * (attempt + 1))
        raise GitHubError(f"GET {url}: {error}")

    def get_all(self, path: str, limit: int = 1000) -> list:
        sep = "&" if "?" in path else "?"
        items: list = []
        page = 1
        while len(items) < limit:
            chunk = self.get(f"{path}{sep}per_page=100&page={page}")
            items.extend(chunk)
            if len(chunk) < 100:
                break
            page += 1
        return items[:limit]


# ---------------------------------------------------------------- identity (session signatures)

BODY_LINE = re.compile(r'Claude session "([^"]*)" \u00b7 ([0-9a-f]{8})')


class Identity:
    """Session signatures and titles: the body line `Claude session "<title>" · <id8>`
    and the commit trailer `Claude-Session: <id8> "<title>"`."""

    @staticmethod
    def config_dir() -> Path:
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")

    @staticmethod
    def normalise(text: str) -> str:
        return text.replace("\r", "")

    @staticmethod
    def signers(text: str) -> List[Tuple[str, str]]:
        return [(m.group(2), m.group(1)) for m in BODY_LINE.finditer(text)]

    @staticmethod
    def short_id(sid: str) -> str:
        return sid[:8]

    @classmethod
    def title(cls, sid: str) -> str:
        """The session's latest custom title or agent name from its transcript, else its session file name."""
        title = None
        for path in (cls.config_dir() / "projects").glob(f"*/{sid}*.jsonl"):
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"custom-title"' in line or '"agent-name"' in line:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        title = rec.get("customTitle") or rec.get("agentName") or title
        if title:
            return title
        for path in (cls.config_dir() / "sessions").glob("*.json"):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (rec.get("sessionId") or "").startswith(sid) and rec.get("name"):
                title = rec["name"]
        return title or "untitled"


def load_identity(checkout: Path):
    return Identity, None, None


def signers(ident, text: str) -> List[dict]:
    return [{"id": s, "title": t}
            for s, t in dict.fromkeys(ident.signers(ident.normalise(text or "")))]


def first_line(body: str) -> str:
    for line in (body or "").replace("\r", "").split("\n"):
        if line.strip():
            return line.strip()[:160]
    return ""


# ---------------------------------------------------------------- ledger (what the model read)

def load_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"comments": {}}


def save_ledger(ledger: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(LEDGER)


def reading(pr: dict, ledger: dict) -> dict:
    """The recorded readings of this PR's comments: the latest review, the
    comments nobody has recorded yet, and whether the reviewed head is current."""
    recs = [dict(ledger["comments"][c["id"]], comment=c["id"])
            for c in pr["comments"] if c["id"] in ledger["comments"]]
    reviews = sorted((r for r in recs if r["kind"] == "review"), key=lambda r: r["at"])
    latest = reviews[-1] if reviews else None
    if latest:
        head = latest.get("head") or ""
        latest = dict(latest, current=bool(head) and pr["head_sha"].startswith(head))
    unread = [c["id"] for c in pr["comments"] if c["id"] not in ledger["comments"]]
    asks = [dict(r, comment=r["comment"]) for r in recs if r.get("asks_note_on")]
    return {"latest_review": latest, "reviews": reviews, "unread": unread, "asks": asks}


# ---------------------------------------------------------------- PR facts

def pr_record(gh: GitHub, ident, number: int) -> dict:
    detail = gh.get(f"pulls/{number}")
    if detail.get("state") == "open" and detail.get("mergeable") is None:
        time.sleep(3)
        detail = gh.get(f"pulls/{number}")
    head = detail["head"]["sha"]
    issue_comments = gh.get_all(f"issues/{number}/comments")
    native = gh.get_all(f"pulls/{number}/reviews")
    commits = gh.get_all(f"pulls/{number}/commits", limit=250)
    checks = gh.get(f"commits/{head}/check-runs?per_page=100").get("check_runs", [])

    comments = [{"id": f"c{c['id']}", "at": c["created_at"], "login": c["user"]["login"],
                 "body": c.get("body") or "", "url": c.get("html_url")} for c in issue_comments]
    comments += [{"id": f"r{r['id']}", "at": r.get("submitted_at") or "", "login": r["user"]["login"],
                  "body": r.get("body") or "", "url": r.get("html_url"),
                  "github_state": r.get("state")} for r in native if r.get("body")]
    comments.sort(key=lambda c: c["at"])
    for c in comments:
        c["signers"] = signers(ident, c["body"])
        c["first_line"] = first_line(c["body"])
        c["chars"] = len(c["body"])

    return {
        "number": number, "title": detail["title"], "url": detail["html_url"],
        "state": detail["state"], "draft": detail.get("draft", False),
        "merged_at": detail.get("merged_at"), "closed_at": detail.get("closed_at"),
        "created_at": detail["created_at"], "updated_at": detail["updated_at"],
        "head_ref": detail["head"]["ref"], "head_sha": head,
        "base_ref": detail["base"]["ref"], "base_sha": detail["base"]["sha"],
        "mergeable": detail.get("mergeable"), "mergeable_state": detail.get("mergeable_state"),
        "additions": detail.get("additions"), "deletions": detail.get("deletions"),
        "changed_files": detail.get("changed_files"),
        "body_signers": signers(ident, detail.get("body") or ""),
        "commits": [{"sha": c["sha"][:7], "full": c["sha"],
                     "subject": c["commit"]["message"].split("\n", 1)[0][:90],
                     "at": c["commit"]["committer"]["date"], "signers": None} for c in commits],
        "comments": comments,
        "checks": latest_checks(checks),
        "files": [f["filename"] for f in gh.get_all(f"pulls/{number}/files", limit=3000)],
    }


def fill_commit_signers(checkout: Path, prs: List[dict]) -> List[str]:
    """Commit signers from git's trailer parser, for every PR's commits."""
    notes = []

    def one(p):
        return p, commit_signers(checkout, [c["full"] for c in p["commits"]])

    with ThreadPoolExecutor(max_workers=8) as pool:
        for p, found in pool.map(one, prs):
            for c in p["commits"]:
                c["signers"] = found.get(c["sha"])
            missing = [c["sha"] for c in p["commits"] if c["signers"] is None]
            if missing:
                notes.append(f"#{p['number']}: {len(missing)} commit(s) not in the local clone, "
                             f"signers unknown ({', '.join(missing[:3])})")
    return notes


def latest_checks(runs: List[dict]) -> List[dict]:
    """One row per check name: the run GitHub created last (highest id)."""
    latest: Dict[str, dict] = {}
    for c in runs:
        if c["name"] not in latest or c["id"] > latest[c["name"]]["id"]:
            latest[c["name"]] = c
    return [{"name": c["name"], "status": c["status"], "conclusion": c["conclusion"], "id": c["id"]}
            for c in sorted(latest.values(), key=lambda c: c["name"])]


def authorship(pr: dict) -> dict:
    """From signatures only: who signed the body, the earliest and the head
    commit. Unsigned stays unsigned; the transcript touches are listed apart."""
    signed_commits = [c for c in pr["commits"] if c["signers"]]
    head_commit = pr["commits"][-1] if pr["commits"] else None
    return {"body": pr["body_signers"][-1] if pr["body_signers"] else None,
            "earliest_signed_commit": ({"sha": signed_commits[0]["sha"], **signed_commits[0]["signers"][-1]}
                                       if signed_commits else None),
            "head_commit": (head_commit["signers"][-1] if head_commit and head_commit["signers"] else None),
            "head_commit_known": bool(head_commit) and head_commit["signers"] is not None}


def stack(prs: Dict[int, dict], closed_by_head: Dict[str, dict], default_branch: str) -> None:
    by_head = {p["head_ref"]: n for n, p in prs.items()}
    for n, p in prs.items():
        below, seen, base = [], set(), p["base_ref"]
        while True:
            if base == default_branch:
                root = {"kind": "main", "branch": base}
                break
            if base in by_head and base not in seen:
                seen.add(base)
                q = prs[by_head[base]]
                below.append(q["number"])
                base = q["base_ref"]
                continue
            closed = closed_by_head.get(base)
            if closed:
                root = {"kind": "merged PR" if closed.get("merged_at") else "closed, unmerged PR",
                        "number": closed["number"], "branch": base}
            else:
                root = {"kind": "branch without a PR", "branch": base}
            break
        p["below"] = below
        p["root"] = root


# ---------------------------------------------------------------- collisions (git + ast)

def git(path, *args, timeout: int = 180) -> Tuple[int, str]:
    try:
        proc = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        return proc.returncode, proc.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


def top_level(source: str) -> Optional[Dict[str, str]]:
    """Top-level function and class sources by name, or None if it does not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = ast.get_source_segment(source, node) or ""
    return out


def function_changes(base_src: Optional[str], head_src: Optional[str]) -> Optional[dict]:
    """Top-level functions/classes a change edits, removes and adds, by
    comparing each one's source at the merge base and at the head."""
    base = top_level(base_src) if base_src is not None else {}
    head = top_level(head_src) if head_src is not None else {}
    if base is None or head is None:
        return None
    return {"edited": sorted(n for n in set(base) & set(head) if base[n] != head[n]),
            "removed": sorted(set(base) - set(head)),
            "added": sorted(set(head) - set(base))}


class Sources:
    """Files at a commit, from the local clone (after `git fetch`)."""

    def __init__(self, checkout: Path):
        self.checkout = checkout
        self.cache: Dict[Tuple[str, str], Optional[str]] = {}
        self.bases: Dict[Tuple[str, str], Optional[str]] = {}

    def merge_base(self, a: str, b: str) -> Optional[str]:
        if (a, b) not in self.bases:
            rc, out = git(self.checkout, "merge-base", a, b)
            self.bases[(a, b)] = out.strip() if rc == 0 else None
        return self.bases[(a, b)]

    def show(self, sha: str, path: str) -> Optional[str]:
        if (sha, path) not in self.cache:
            rc, out = git(self.checkout, "show", f"{sha}:{path}")
            self.cache[(sha, path)] = out if rc == 0 else None
        return self.cache[(sha, path)]

    def exists(self, sha: str) -> bool:
        return git(self.checkout, "cat-file", "-e", f"{sha}^{{commit}}")[0] == 0


def collisions(prs: Dict[int, dict], checkout: Path, only: Optional[int] = None) -> Tuple[list, list]:
    """For open PRs in different stacks: the files both touch, and for shared
    .py files the top-level functions one removes (moves) while the other
    edits, or both edit."""
    src = Sources(checkout)
    per: Dict[Tuple[int, str], Optional[dict]] = {}
    notes: List[str] = []

    def changes(n: int, path: str) -> Optional[dict]:
        if (n, path) not in per:
            p = prs[n]
            if not src.exists(p["head_sha"]) or not src.exists(p["base_sha"]):
                notes.append(f"#{n}: head or base commit not in the local clone (fetch)")
                per[(n, path)] = None
            else:
                mb = src.merge_base(p["base_sha"], p["head_sha"])
                per[(n, path)] = function_changes(src.show(mb, path) if mb else None,
                                                  src.show(p["head_sha"], path))
        return per[(n, path)]

    def added_elsewhere(n: int, fn: str, not_in: str) -> List[str]:
        return [f for f in prs[n]["files"] if f.endswith(".py") and f != not_in
                and fn in ((changes(n, f) or {}).get("added") or [])]

    out = []
    nums = sorted(prs)
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            if only is not None and only not in (a, b):
                continue
            pa, pb = prs[a], prs[b]
            if a in pb["below"] or b in pa["below"]:
                continue
            shared = sorted(set(pa["files"]) & set(pb["files"]))
            if not shared:
                continue
            moves, both, unparsed = [], [], []
            for path in (s for s in shared if s.endswith(".py")):
                ca, cb = changes(a, path), changes(b, path)
                if ca is None or cb is None:
                    unparsed.append(path)
                    continue
                for x, y, cx, cy in ((a, b, ca, cb), (b, a, cb, ca)):
                    for fn in sorted(set(cx["removed"]) & set(cy["edited"])):
                        moves.append({"remover": x, "editor": y, "file": path, "function": fn,
                                      "moved_to": added_elsewhere(x, fn, path)})
                for fn in sorted(set(ca["edited"]) & set(cb["edited"])):
                    both.append({"file": path, "function": fn})
            out.append({"a": a, "b": b, "shared_files": shared, "moves": moves,
                        "both_edit": both, "not_compared": unparsed})
    return out, sorted(set(notes))


# ---------------------------------------------------------------- sessions

def _transcripts(ident) -> List[Path]:
    projects = ident.config_dir() / "projects"
    return list(projects.glob("*/*.jsonl")) + list(projects.glob("*/*/subagents/*.jsonl"))


def _session_of(path: Path) -> str:
    return path.parent.parent.name if path.parent.name == "subagents" else path.stem


def _pr_touch_file(job) -> Tuple[str, Dict[int, dict]]:
    """One transcript: per PR, Claude Code's own pr-link records for it and
    the entries that carry its URL. Structured identifiers only."""
    path, repo, numbers = job
    wanted = set(numbers)
    url = re.compile(rf"github\.com/{re.escape(repo)}/pull/(\d+)")
    hits: Dict[int, dict] = {}

    def add(n, kind, stamp):
        row = hits.setdefault(n, {"first": "", "last": "", "mentions": 0, "linked": 0})
        row[kind] += 1
        row["first"] = min(row["first"] or stamp or "", stamp or row["first"])
        row["last"] = max(row["last"], stamp or "")

    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return path, {}
    with fh:
        for line in fh:
            if "/pull/" not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            stamp = rec.get("timestamp", "") if isinstance(rec, dict) else ""
            if isinstance(rec, dict) and rec.get("type") == "pr-link":
                if rec.get("prRepository") == repo and rec.get("prNumber") in wanted:
                    add(rec["prNumber"], "linked", stamp)
                continue
            for n in {int(m) for m in url.findall(line)} & wanted:
                add(n, "mentions", stamp)
    return path, hits


def pr_sessions(ident, numbers: List[int], repo: str) -> Dict[int, List[dict]]:
    """Per PR, the sessions whose transcripts link or name it, first touch first."""
    out: Dict[int, Dict[str, dict]] = {}
    if not numbers:
        return {}
    jobs = [(str(f), repo, list(numbers)) for f in _transcripts(ident)]
    with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
        for path, hits in pool.map(_pr_touch_file, jobs, chunksize=1):
            sid = _session_of(Path(path))
            if sid == OWN_SESSION:
                continue
            for n, row in hits.items():
                t = out.setdefault(n, {}).setdefault(sid, {"first": "", "last": "", "mentions": 0, "linked": 0})
                t["mentions"] += row["mentions"]
                t["linked"] += row["linked"]
                t["first"] = min(t["first"] or row["first"], row["first"] or t["first"])
                t["last"] = max(t["last"], row["last"])
    return {n: [dict(id=sid, **t) for sid, t in sorted(rows.items(), key=lambda kv: kv[1]["first"])]
            for n, rows in out.items()}


def _worktree_file(job) -> Tuple[str, Dict[str, dict]]:
    """One transcript: per worktree, how many files the session edited in it
    (tool_use file_path) and how many entries ran with it as the working
    directory (entry cwd). Structured fields only; command text is not read."""
    path, roots = job
    roots = {r: Path(r) for r in roots}
    names = {r: Path(r).name for r in roots}
    hits: Dict[str, dict] = {}

    def add(root, kind, stamp):
        row = hits.setdefault(root, {"edited": 0, "cwd": 0, "last": ""})
        row[kind] += 1
        row["last"] = max(row["last"], stamp or "")

    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return path, {}
    with fh:
        for line in fh:
            candidates = [r for r, name in names.items() if name in line]
            if not candidates:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            stamp = entry.get("timestamp", "")
            cwd = entry.get("cwd")
            for r in candidates:
                if cwd and Path(cwd).is_relative_to(roots[r]):
                    add(r, "cwd", stamp)
            if entry.get("type") != "assistant":
                continue
            for block in (entry.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
                    continue
                target = (block.get("input") or {}).get("file_path")
                if not target:
                    continue
                for r in candidates:
                    if Path(target).is_relative_to(roots[r]):
                        add(r, "edited", stamp)
    return path, hits


def worktree_sessions(ident, roots: List[str]) -> Dict[str, List[dict]]:
    out: Dict[str, Dict[str, dict]] = {}
    if not roots:
        return {}
    jobs = [(str(f), roots) for f in _transcripts(ident)]
    with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
        for path, hits in pool.map(_worktree_file, jobs, chunksize=1):
            sid = _session_of(Path(path))
            if sid == OWN_SESSION:
                continue
            for root, row in hits.items():
                t = out.setdefault(root, {}).setdefault(sid, {"edited": 0, "cwd": 0, "last": ""})
                t["edited"] += row["edited"]
                t["cwd"] += row["cwd"]
                t["last"] = max(t["last"], row["last"])
    return {root: [dict(id=sid, **t) for sid, t in
                   sorted(rows.items(), key=lambda kv: (-kv[1]["edited"], -kv[1]["cwd"]))]
            for root, rows in out.items()}


def commit_signers(checkout: Path, shas: List[str]) -> Dict[str, Optional[List[dict]]]:
    """Claude-Session trailers of each commit, parsed by git itself. A commit
    missing from the local clone maps to None (unknown), not to unsigned."""
    fmt = "--format=%h%x1f%(trailers:key=Claude-Session,valueonly,separator=%x1e)%x1d"

    def parse(out: str) -> Dict[str, List[dict]]:
        res = {}
        for rec in out.split("\x1d"):
            rec = rec.strip("\n")
            if not rec:
                continue
            sha, _, trailers = rec.partition("\x1f")
            rows = []
            for value in (v.strip() for v in trailers.split("\x1e")):
                if value:
                    sid, _, title = value.partition(" ")
                    rows.append({"id": sid, "title": title.strip().strip('"')})
            res[sha[:7]] = rows
        return res

    if not shas:
        return {}
    rc, out = git(checkout, "show", "-s", "--abbrev=7", fmt, *shas)
    if rc == 0:
        found = parse(out)
    else:
        found = {}
        for sha in shas:
            rc1, out1 = git(checkout, "show", "-s", "--abbrev=7", fmt, sha)
            if rc1 == 0:
                found.update(parse(out1))
    return {sha[:7]: found.get(sha[:7]) for sha in shas}


def live_sessions(ident) -> Dict[str, dict]:
    out = {}
    for path in (ident.config_dir() / "sessions").glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("sessionId") and _alive(rec.get("pid")):
            out[rec["sessionId"]] = {"status": rec.get("status", "running"),
                                     "updated": rec.get("statusUpdatedAt") or rec.get("updatedAt")}
    return out


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def session_table(ident, live: Dict[str, dict], pr_rows: Dict[int, List[dict]],
                  wt_rows: Dict[str, List[dict]], prs: Dict[int, dict]) -> Dict[str, dict]:
    """Every session that is open, touched a PR or a worktree, or signed
    something on an open PR; last activity is its transcript's modified time."""
    mtimes: Dict[str, float] = {}
    for f in _transcripts(ident):
        sid = _session_of(f)
        try:
            mtimes[sid] = max(mtimes.get(sid, 0.0), f.stat().st_mtime)
        except OSError:
            pass
    signed = set()
    for p in prs.values():
        for s in p["body_signers"]:
            signed.add(s["id"])
        for c in p["commits"] + p["comments"]:
            for s in c["signers"] or []:
                signed.add(s["id"])
    ids = set(live)
    ids |= {r["id"] for rows in pr_rows.values() for r in rows}
    ids |= {r["id"] for rows in wt_rows.values() for r in rows}
    ids |= {sid for sid in mtimes if ident.short_id(sid) in signed}

    def row(sid):
        rec = live.get(sid)
        stamp = (datetime.fromtimestamp(mtimes[sid], timezone.utc).isoformat(timespec="seconds")
                 if sid in mtimes else "")
        return ident.short_id(sid), {
            "session_id": sid, "title": ident.title(sid), "open": bool(rec),
            "status": rec["status"] if rec else "closed",
            "status_since": _ms(rec["updated"]) if rec and rec.get("updated") else None,
            "last_active": stamp, "this_tracker": sid == OWN_SESSION}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(row, sorted(ids)))


def _ms(value) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- worktrees

def worktrees(checkout: Path, include_detached: bool) -> dict:
    rc, out = git(checkout, "worktree", "list", "--porcelain")
    if rc != 0:
        return {"error": out.strip()[:200], "items": [], "detached_skipped": 0}
    entries, cur = [], {}
    for line in out.split("\n") + [""]:
        if not line.strip():
            if cur:
                entries.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur["path"] = val
        elif key == "HEAD":
            cur["head"] = val[:7]
        elif key == "branch":
            cur["branch"] = val.replace("refs/heads/", "", 1)
    skipped = sum(1 for e in entries if "branch" not in e and not include_detached)
    todo = [e for e in entries if "branch" in e or include_detached]
    with ThreadPoolExecutor(max_workers=8) as pool:
        items = list(pool.map(_inspect_worktree, todo))
    return {"items": items, "detached_skipped": skipped}


def _count(path, rng: str) -> int:
    rc, out = git(path, "rev-list", "--count", rng)
    return int(out.strip()) if rc == 0 and out.strip().isdigit() else 0


def _inspect_worktree(e: dict) -> dict:
    """Facts only: uncommitted files, and how the branch stands against origin.
    For a branch that diverged, git's own patch-id check (`git cherry`) says
    how many local commits have no equivalent patch on origin."""
    path = e["path"]
    if not Path(path).exists():
        e.update(missing=True, modified=[], untracked=[], has_unique=False)
        return e
    rc, st = git(path, "status", "--porcelain=v1", "--untracked-files=normal")
    lines = [l for l in st.split("\n") if l.strip()] if rc == 0 else []
    e["modified"] = [l[3:] for l in lines if not l.startswith("??")]
    e["untracked"] = [l[3:] for l in lines if l.startswith("??")]
    e["last_commit"] = git(path, "log", "-1", "--format=%cI")[1].strip()
    b = e.get("branch")
    e["origin"] = None
    if b:
        remote = f"refs/remotes/origin/{b}"
        if git(path, "rev-parse", "-q", "--verify", remote)[0] != 0:
            e["origin"] = {"exists": False,
                           "merged_into_main": git(path, "merge-base", "--is-ancestor", b, "origin/main")[0] == 0,
                           "commits_not_on_main": _count(path, f"origin/main..{b}")}
        else:
            ahead, behind = _count(path, f"{remote}..{b}"), _count(path, f"{b}..{remote}")
            row = {"exists": True, "ahead": ahead, "behind": behind}
            if ahead:
                cherry = git(path, "cherry", remote, b)[1].split("\n")
                row["patches_not_on_origin"] = sum(1 for l in cherry if l.startswith("+"))
                row["local_subjects"] = [s for s in git(path, "log", "--format=%h %s", f"{remote}..{b}")[1]
                                         .split("\n") if s][:8]
            e["origin"] = row
    o = e["origin"] or {}
    e["has_unique"] = bool(
        e["modified"] or e["untracked"] or
        (o.get("exists") is False and not o.get("merged_into_main") and o.get("commits_not_on_main")) or
        o.get("ahead"))
    return e


# ---------------------------------------------------------------- sweep

def fetch_prs(gh: GitHub, ident, numbers: List[int]) -> Tuple[Dict[int, dict], List[str]]:
    prs, errors = {}, []

    def one(n):
        try:
            return n, pr_record(gh, ident, n), None
        except (GitHubError, KeyError, ValueError) as exc:
            return n, None, str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for n, rec, err in pool.map(one, numbers):
            if rec:
                prs[n] = rec
            else:
                errors.append(f"PR #{n}: {err}")
    return prs, errors


def repo_basics(gh: GitHub):
    try:
        info = gh.get(f"{API}/repos/{gh.repo}")
        listing = gh.get_all("pulls?state=open")
        closed = gh.get_all("pulls?state=closed&sort=updated&direction=desc", limit=300)
    except GitHubError as exc:
        raise CouldNotRun(str(exc))
    closed_by_head: Dict[str, dict] = {}
    for c in closed:
        closed_by_head.setdefault(c["head"]["ref"], c)
    return info.get("default_branch", "main"), listing, closed, closed_by_head


def sweep(args) -> dict:
    ident, whose, tools_dir = load_identity(args.checkout)
    gh = GitHub(args.repo)
    default_branch, listing, closed, closed_by_head = repo_basics(gh)
    errors: List[str] = []
    fetched = None
    if not args.no_fetch:
        rc, out = git(args.checkout, "fetch", "--quiet", "--prune", "origin", timeout=300)
        fetched = rc == 0
        if rc != 0:
            errors.append(f"git fetch failed: {out.strip()[:200]}")
    try:
        issues = [i for i in gh.get_all("issues?state=open") if "pull_request" not in i]
        main_commits = gh.get(f"commits?sha={default_branch}&per_page=5")
    except GitHubError as exc:
        raise CouldNotRun(str(exc))

    prs, errs = fetch_prs(gh, ident, [p["number"] for p in listing])
    errors += errs
    stack(prs, closed_by_head, default_branch)
    col, col_notes = collisions(prs, args.checkout)
    errors += col_notes
    ledger = load_ledger()

    wt = {"items": [], "detached_skipped": 0}
    if not args.no_worktrees:
        wt = worktrees(args.checkout, args.all_worktrees)
        if wt.get("error"):
            errors.append(f"worktrees: {wt['error']}")
    head_to_pr = {p["head_ref"]: n for n, p in prs.items()}
    for e in wt["items"]:
        if e.get("branch") in head_to_pr:
            e["pr"] = head_to_pr[e["branch"]]

    errors += fill_commit_signers(args.checkout, list(prs.values()))
    pr_rows: Dict[int, List[dict]] = {}
    wt_rows: Dict[str, List[dict]] = {}
    if not args.no_owners:
        pr_rows = pr_sessions(ident, sorted(prs), args.repo)
        roots = [e["path"] for e in wt["items"]
                 if e.get("has_unique") and Path(e["path"]) != Path(args.checkout)]
        wt_rows = worktree_sessions(ident, roots)
    sessions = session_table(ident, live_sessions(ident), pr_rows, wt_rows, prs)

    for n, p in prs.items():
        p["authorship"] = authorship(p)
        p["reading"] = reading(p, ledger)
        p["touched_by"] = pr_rows.get(n, [])
        for c in p["comments"]:
            c.pop("body", None)
    for e in wt["items"]:
        e["sessions"] = wt_rows.get(e["path"], [])

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.closed_days)).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": args.repo, "default_branch": default_branch, "fetched": fetched,
        "main": [{"sha": c["sha"][:7], "subject": c["commit"]["message"].split("\n", 1)[0][:90],
                  "at": c["commit"]["committer"]["date"]} for c in main_commits],
        "prs": {str(n): p for n, p in sorted(prs.items(), reverse=True)},
        "check_names": sorted({c["name"] for p in prs.values() for c in p["checks"]}),
        "collisions": [c for c in col if c["moves"] or c["both_edit"]],
        "shared_files_only": [{"a": c["a"], "b": c["b"], "files": c["shared_files"]}
                              for c in col if not (c["moves"] or c["both_edit"])],
        "recent_closed": [{"number": c["number"], "title": c["title"], "closed_at": c["closed_at"],
                           "merged_at": c.get("merged_at")}
                          for c in closed if (c.get("closed_at") or "") >= cutoff],
        "issues": [{"number": i["number"], "title": i["title"], "updated_at": i["updated_at"]}
                   for i in issues],
        "sessions": sessions,
        "worktrees": wt,
        "errors": errors,
    }


# ---------------------------------------------------------------- pr mode

def pr_mode(args) -> dict:
    ident, whose, tools_dir = load_identity(args.checkout)
    gh = GitHub(args.repo)
    default_branch, listing, closed, closed_by_head = repo_basics(gh)
    notes: List[str] = []
    if not args.no_fetch:
        rc, out = git(args.checkout, "fetch", "--quiet", "--prune", "origin", timeout=300)
        if rc != 0:
            notes.append(f"git fetch failed: {out.strip()[:200]}")
    try:
        target = pr_record(gh, ident, args.number)
    except GitHubError as exc:
        raise CouldNotRun(str(exc))
    notes += fill_commit_signers(args.checkout, [target])
    if args.comments_only:
        target["reading"] = reading(target, load_ledger())
        show = set(target["reading"]["unread"]) if args.comments == "unread" else (
            {c["id"] for c in target["comments"]} if args.comments == "all" else set())
        for c in target["comments"]:
            if c["id"] not in show:
                c.pop("body", None)
        return {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "pr": target, "comments_only": True, "notes": notes}

    def light(p):
        return p["number"], {"number": p["number"], "title": p["title"], "head_ref": p["head"]["ref"],
                             "head_sha": p["head"]["sha"], "base_ref": p["base"]["ref"],
                             "base_sha": p["base"]["sha"],
                             "files": [f["filename"] for f in gh.get_all(f"pulls/{p['number']}/files", limit=3000)]}

    allp: Dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for n, rec in pool.map(light, [p for p in listing if p["number"] != args.number]):
            allp[n] = rec
    allp[args.number] = target
    stack(allp, closed_by_head, default_branch)
    col, col_notes = collisions(allp, args.checkout, only=args.number)
    notes += col_notes
    rows = pr_sessions(ident, [args.number], args.repo).get(args.number, [])
    live = live_sessions(ident)
    ledger = load_ledger()
    target["authorship"] = authorship(target)
    target["reading"] = reading(target, ledger)
    target["children"] = [n for n, p in allp.items() if p["base_ref"] == target["head_ref"]]
    target["touched_by"] = [dict(t, title=ident.title(t["id"]),
                                 status=live[t["id"]]["status"] if t["id"] in live else "closed")
                            for t in rows]
    show = set(target["reading"]["unread"]) if args.comments == "unread" else (
        {c["id"] for c in target["comments"]} if args.comments == "all" else set())
    for c in target["comments"]:
        if c["id"] not in show:
            c.pop("body", None)
    return {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pr": target, "collisions": [c for c in col if c["moves"] or c["both_edit"]],
            "shared_files_only": [{"other": c["b"] if c["a"] == args.number else c["a"],
                                   "files": c["shared_files"]} for c in col
                                  if not (c["moves"] or c["both_edit"])],
            "notes": notes, "titles": {n: p["title"] for n, p in allp.items()}}


# ---------------------------------------------------------------- record

def record(args) -> dict:
    if args.kind == "review" and not args.verdict:
        raise Refused("--kind review needs --verdict (PASS, BLOCKED or UNCLEAR)")
    if args.kind != "review" and (args.verdict or args.round or args.head):
        raise Refused("--verdict, --round and --head belong to --kind review")
    ident, _, _ = load_identity(args.checkout)
    gh = GitHub(args.repo)
    try:
        pr = pr_record(gh, ident, args.number)
    except GitHubError as exc:
        raise CouldNotRun(str(exc))
    comment = next((c for c in pr["comments"] if c["id"] == args.comment), None)
    if not comment:
        ids = ", ".join(c["id"] for c in pr["comments"]) or "none"
        raise Refused(f"#{args.number} has no comment {args.comment} (its comments: {ids})")
    if args.head and not any(pr["head_sha"].startswith(args.head) or c["sha"].startswith(args.head[:7])
                             for c in pr["commits"]):
        print(f"note: {args.head} is not a commit of #{args.number} today (a force-push can do this)",
              file=sys.stderr)
    ledger = load_ledger()
    entry = {"pr": args.number, "at": comment["at"], "kind": args.kind, "summary": args.summary,
             "signers": comment["signers"], "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if args.kind == "review":
        entry.update(verdict=args.verdict, round=args.round, head=args.head, net_value=args.net)
    if args.asks_note_on:
        entry["asks_note_on"] = args.asks_note_on
    ledger.setdefault("comments", {})[args.comment] = entry
    save_ledger(ledger)
    return {"recorded": args.comment, **entry}


# ---------------------------------------------------------------- session logs

def _message_text(entry: dict) -> str:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text")


def session_log(path: Path, turns: int) -> dict:
    """The last `turns` owner messages and the session's last reply after each, from one transcript."""
    pairs: List[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"type":"user"' not in line and '"type":"assistant"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("isSidechain") or entry.get("isMeta"):
                continue
            text = _message_text(entry).strip()
            if not text or text.startswith("<"):
                continue
            if entry.get("type") == "user":
                pairs.append({"at": entry.get("timestamp", ""), "owner": text, "reply": "", "reply_at": ""})
            elif pairs:
                pairs[-1].update(reply=text, reply_at=entry.get("timestamp", ""))
    return {"turns": pairs[-turns:]}


def sessions_mode(args) -> dict:
    ident = Identity
    live = live_sessions(ident)
    cutoff = time.time() - args.hours * 3600
    out = {}
    for f in (ident.config_dir() / "projects").glob("*/*.jsonl"):
        sid = f.stem
        if sid == OWN_SESSION or (sid not in live and f.stat().st_mtime < cutoff):
            continue
        if args.only and not any(sid.startswith(o) for o in args.only):
            continue
        row = session_log(f, args.turns)
        row.update(title=ident.title(sid), status=live[sid]["status"] if sid in live else "closed",
                   last_active=datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"))
        out[sid[:8]] = row
    return {"sessions": dict(sorted(out.items(), key=lambda kv: kv[1]["last_active"], reverse=True))}


def print_sessions(data: dict, width: int) -> None:
    for sid, s in data["sessions"].items():
        print(f"\n===== \"{s['title']}\" ({sid}) {s['status']}; last active {local(s['last_active'])}")
        for t in s["turns"]:
            print(f"--- owner {local(t['at'])}: {t['owner'][:width]}")
            print(f"--- reply {local(t['reply_at'])}: {t['reply'][:width * 4] or '(none yet)'}")


# ---------------------------------------------------------------- snapshots + diff

def save_snapshot(data: dict) -> Path:
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def snapshots() -> List[Path]:
    return sorted(SNAPSHOTS.glob("*.json")) if SNAPSHOTS.is_dir() else []


def check_state(checks: List[dict]) -> str:
    return ", ".join(f"{c['name']}={c['conclusion'] or c['status']}" for c in sorted(checks, key=lambda c: c["name"]))


def diff(old: dict, new: dict) -> List[str]:
    out: List[str] = []
    om, nm = (old.get("main") or [{}])[0].get("sha"), (new.get("main") or [{}])[0].get("sha")
    if om != nm:
        out.append(f"{new['default_branch']} moved {om} -> {nm}: {new['main'][0]['subject']}")
    op, np_ = old.get("prs", {}), new.get("prs", {})
    closed = {str(c["number"]): c for c in new.get("recent_closed", [])}
    for n in sorted(set(np_) - set(op), key=int):
        out.append(f"new PR #{n}: {np_[n]['title'][:80]}")
    for n in sorted(set(op) - set(np_), key=int):
        c = closed.get(n)
        how = "merged" if c and c.get("merged_at") else "closed without merging" if c else "no longer open"
        out.append(f"#{n} {how}: {op[n]['title'][:70]}")
    for n in sorted(set(op) & set(np_), key=int):
        a, b = op[n], np_[n]
        if a["head_sha"] != b["head_sha"]:
            out.append(f"#{n} head {a['head_sha'][:7]} -> {b['head_sha'][:7]}")
        if a["base_ref"] != b["base_ref"]:
            out.append(f"#{n} retargeted {a['base_ref']} -> {b['base_ref']}")
        if a.get("mergeable_state") != b.get("mergeable_state"):
            out.append(f"#{n} merge state {a.get('mergeable_state')} -> {b.get('mergeable_state')}")
        if check_state(a["checks"]) != check_state(b["checks"]):
            out.append(f"#{n} CI now: {check_state(b['checks']) or 'no runs'}")
        new_ids = [c for c in b["comments"] if c["id"] not in {x["id"] for x in a["comments"]}]
        for c in new_ids:
            who = ", ".join(f"\"{s['title']}\" ({s['id']})" for s in c["signers"]) or "unsigned"
            out.append(f"#{n} new comment {c['id']} by {who}: {c['first_line'][:90]}")
    oi = {i["number"] for i in old.get("issues", [])}
    ni = {i["number"]: i for i in new.get("issues", [])}
    out += [f"new issue #{n}: {ni[n]['title'][:80]}" for n in sorted(set(ni) - oi)]
    out += [f"issue #{n} closed" for n in sorted(oi - set(ni))]
    os_, ns = old.get("sessions", {}), new.get("sessions", {})
    for sid in sorted(set(ns) | set(os_)):
        a, b = os_.get(sid), ns.get(sid)
        if b and b["open"] and not (a and a.get("open")):
            out.append(f"session \"{b['title']}\" ({sid}) open, {b['status']}")
        elif a and b and a["status"] != b["status"]:
            out.append(f"session \"{b['title']}\" ({sid}) {a['status']} -> {b['status']}")
    key = lambda e: e["path"]
    ow = {key(e): e for e in old.get("worktrees", {}).get("items", []) if e.get("has_unique")}
    nw = {key(e): e for e in new.get("worktrees", {}).get("items", []) if e.get("has_unique")}
    out += [f"new local-only work in {Path(p).name} [{nw[p].get('branch')}]" for p in sorted(set(nw) - set(ow))]
    out += [f"local work in {Path(p).name} [{ow[p].get('branch')}] no longer local-only" for p in sorted(set(ow) - set(nw))]
    for p in sorted(set(ow) & set(nw)):
        a, b = ow[p], nw[p]
        if (len(a["modified"]), len(a["untracked"]), a.get("origin")) != (len(b["modified"]), len(b["untracked"]), b.get("origin")):
            out.append(f"{Path(p).name}: modified {len(b['modified'])}, untracked {len(b['untracked'])}, origin {b.get('origin')}")
    return out


# ---------------------------------------------------------------- printing

def local(stamp: Optional[str]) -> str:
    if not stamp:
        return "?"
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return stamp[:16]


def who(sig: Optional[dict], sessions: Optional[dict] = None) -> str:
    if not sig:
        return "unsigned"
    title = sessions[sig["id"]]["title"] if sessions and sig["id"] in sessions else sig["title"]
    return f"\"{title}\" ({sig['id']})"


def ci_line(checks: List[dict], names: List[str]) -> str:
    if not checks:
        return "no CI runs on this head"
    present = {c["name"] for c in checks}
    absent = [n for n in names if n not in present]
    line = check_state(checks)
    return line + (f"; not run on this head: {', '.join(absent)}" if absent else "")


def review_line(rd: dict, sessions: Optional[dict] = None) -> str:
    rv = rd["latest_review"]
    unread = f"; {len(rd['unread'])} comment(s) not yet read: {', '.join(rd['unread'])}" if rd["unread"] else ""
    if not rv:
        return f"no review recorded{unread}"
    signer = who(rv["signers"][-1], sessions) if rv.get("signers") else "unsigned"
    rnd = f"round {rv['round']}" if rv.get("round") else "round ?"
    head = rv.get("head") or "head not recorded"
    state = "current head" if rv["current"] else "HEAD MOVED since"
    net = f"; {rv['net_value']}" if rv.get("net_value") else ""
    return f"{rv['verdict']} {rnd} by {signer} {local(rv['at'])} at {head} ({state}){net} - {rv['summary']}{unread}"


def print_sweep(data: dict, changes: Optional[List[str]], prev: Optional[str]) -> None:
    S = data["sessions"]
    print(f"Tracker sweep {local(data['taken_at'])} (local) - {data['repo']}"
          f"{' - git fetch FAILED' if data['fetched'] is False else ''}")
    m = data["main"][0] if data["main"] else {}
    print(f"{data['default_branch']} {m.get('sha')} {local(m.get('at'))} {m.get('subject', '')}\n")
    if changes is None:
        print("Changes: first snapshot, nothing to compare with.")
    else:
        print(f"Changes since {prev}:" + ("" if changes else " none."))
        for c in changes:
            print(f"  - {c}")
    print("\nSessions:")
    for sid, s in sorted(S.items(), key=lambda kv: (not kv[1]["open"], kv[1]["status"], kv[1]["title"])):
        state = f"{s['status']} since {local(s['status_since'])}" if s["open"] else "closed"
        mark = " [this tracker]" if s["this_tracker"] else ""
        print(f"  \"{s['title']}\" ({sid}) {state}; last transcript activity {local(s['last_active'])}{mark}")
    prs = {int(n): p for n, p in data["prs"].items()}
    groups: Dict[str, List[int]] = {}
    for n, p in prs.items():
        r = p["root"]
        key = (f"on {r['branch']}" if r["kind"] == "main" else
               f"on {r['kind']} #{r['number']} ({r['branch']})" if "number" in r else
               f"on {r['kind']} {r['branch']}")
        groups.setdefault(key, []).append(n)
    print(f"\nOpen PRs ({len(prs)}), grouped by what their stack rests on:")
    for key in sorted(groups, key=lambda k: (k != f"on {data['default_branch']}", k)):
        print(f"\n  == {key}")
        for n in sorted(groups[key], key=lambda n: (len(prs[n]["below"]), n)):
            p = prs[n]
            a = p["authorship"]
            ind = "  " + "  " * min(len(p["below"]), 16)
            on = f" [on #{p['below'][0]}, depth {len(p['below'])}]" if p["below"] else ""
            print(f"{ind}#{n}{' (draft)' if p['draft'] else ''} {p['head_ref']} -> {p['base_ref']}{on}: {p['title'][:90]}")
            head_by = who(a["head_commit"], S) if a["head_commit_known"] else "unknown (not in local clone)"
            print(f"{ind}   signed: body {who(a['body'], S)}; earliest signed commit "
                  f"{who(a['earliest_signed_commit'], S)}; head {p['head_sha'][:7]} by {head_by}")
            print(f"{ind}   +{p['additions']}/-{p['deletions']} in {p['changed_files']} files; "
                  f"merge {p['mergeable_state']}; updated {local(p['updated_at'])}")
            print(f"{ind}   CI: {ci_line(p['checks'], data['check_names'])}")
            print(f"{ind}   review: {review_line(p['reading'], S)}")
            for ask in p["reading"]["asks"]:
                print(f"{ind}   asks for a note on {', '.join('#' + str(x) for x in ask['asks_note_on'])} ({ask['comment']})")
            if p["touched_by"]:
                rows = [f"\"{S.get(t['id'][:8], {}).get('title', t['id'][:8])}\" ({t['id'][:8]}) "
                        f"{'linked ' + str(t['linked']) + 'x' if t['linked'] else 'mentioned'} "
                        f"last {local(t['last'])} [{S.get(t['id'][:8], {}).get('status', '?')}]"
                        for t in p["touched_by"][-4:]]
                print(f"{ind}   sessions: " + "; ".join(rows))
    if data["collisions"]:
        print("\nCode collisions between PRs in different stacks (ast, merge base vs head):")
        for c in data["collisions"]:
            for mv in c["moves"]:
                dest = f" (added in {', '.join(mv['moved_to'])})" if mv["moved_to"] else ""
                print(f"  - #{mv['remover']} removes {mv['file']}:{mv['function']}{dest}; #{mv['editor']} edits it")
            if c["both_edit"]:
                fns = ", ".join(f"{b['file'].rsplit('/', 1)[-1]}:{b['function']}" for b in c["both_edit"])
                print(f"  - #{c['a']} and #{c['b']} both edit {fns}")
            if c["not_compared"]:
                print(f"    not compared (missing or unparsable): {', '.join(c['not_compared'])}")
    if data["shared_files_only"]:
        print(f"\nFile overlap only (no top-level function both touch): {len(data['shared_files_only'])} "
              f"PR pairs; listed in the snapshot JSON under shared_files_only.")
    if data["recent_closed"]:
        print("\nClosed recently:")
        for c in data["recent_closed"]:
            how = f"merged {local(c['merged_at'])}" if c["merged_at"] else f"closed unmerged {local(c['closed_at'])}"
            print(f"  - #{c['number']} {how}: {c['title'][:80]}")
    if data["issues"]:
        print(f"\nOpen issues ({len(data['issues'])}):")
        for i in sorted(data["issues"], key=lambda i: -i["number"]):
            print(f"  - #{i['number']} {i['title'][:100]}")
    wt = data["worktrees"]
    items = [e for e in wt.get("items", []) if e.get("has_unique")]
    print(f"\nLocal worktrees with something GitHub may not have ({len(items)}; "
          f"{wt.get('detached_skipped', 0)} detached worktrees not inspected):")
    for e in sorted(items, key=lambda e: e.get("last_commit", ""), reverse=True):
        o = e.get("origin") or {}
        if o.get("exists") is False:
            where = ("no origin branch; merged into main" if o.get("merged_into_main")
                     else f"no origin branch; {o.get('commits_not_on_main')} commits not on main")
        elif o.get("exists"):
            where = f"origin: ahead {o['ahead']}, behind {o['behind']}"
            if o.get("ahead"):
                where += f", {o.get('patches_not_on_origin')} of {o['ahead']} patches not on origin (git cherry)"
        else:
            where = "detached"
        pr = f" = PR #{e['pr']}" if e.get("pr") else ""
        print(f"  - {Path(e['path']).name} [{e.get('branch', 'detached')}{pr}] {where}; "
              f"modified {len(e['modified'])}, untracked {len(e['untracked'])}; last commit {local(e.get('last_commit'))}")
        if e["modified"] or e["untracked"]:
            print(f"      files: {', '.join(e['modified'][:5] + ['?' + u for u in e['untracked'][:4]])}")
        if o.get("local_subjects"):
            print(f"      local commits: {' | '.join(o['local_subjects'][:4])}")
        if e.get("sessions"):
            rows = [f"\"{S.get(t['id'][:8], {}).get('title', t['id'][:8])}\" ({t['id'][:8]}) "
                    f"edited {t['edited']} file(s), {t['cwd']} entries with it as cwd, last {local(t['last'])} "
                    f"[{S.get(t['id'][:8], {}).get('status', '?')}]" for t in e["sessions"][:3]]
            print(f"      sessions: " + "; ".join(rows))
    if data["errors"]:
        print("\nErrors (these items are incomplete):")
        for err in data["errors"]:
            print(f"  - {err}")


def print_comments(p: dict) -> None:
    print(f"\n  comments ({len(p['comments'])}; full text below for: "
          f"{', '.join(c['id'] for c in p['comments'] if 'body' in c) or 'none'}):")
    for c in p["comments"]:
        rec = "recorded" if c["id"] not in p["reading"]["unread"] else "NOT READ"
        signed = ", ".join(who(s) for s in c["signers"]) or "unsigned"
        print(f"    - {c['id']} {local(c['at'])} {c['login']} {signed} [{rec}] {c['chars']} chars: {c['first_line'][:100]}")
    for c in p["comments"]:
        if "body" in c:
            print(f"\n===== {c['id']} {local(c['at'])} {', '.join(who(s) for s in c['signers']) or 'unsigned'}\n{c['body']}")


def print_pr(data: dict) -> None:
    p = data["pr"]
    if data.get("comments_only"):
        print(f"#{p['number']} {p['title']}\n  {p['state']}; {p['head_ref']} -> {p['base_ref']}; "
              f"head {p['head_sha'][:7]}; merge {p['mergeable_state']}; CI {check_state(p['checks']) or 'no runs'}")
        print_comments(p)
        return
    p = data["pr"]
    a = p["authorship"]
    print(f"#{p['number']} {p['title']}")
    print(f"  {p['state']}{' (draft)' if p['draft'] else ''}; {p['head_ref']} -> {p['base_ref']}; head {p['head_sha'][:7]}; "
          f"base {p['base_sha'][:7]}; +{p['additions']}/-{p['deletions']} in {p['changed_files']} files; {p['url']}")
    if p.get("merged_at"):
        print(f"  merged {local(p['merged_at'])}")
    print(f"  merge: mergeable={p['mergeable']} state={p['mergeable_state']}")
    print(f"  CI on head: {check_state(p['checks']) or 'no runs'}")
    r = p["root"]
    print(f"  stack: {' <- '.join(['#' + str(p['number'])] + ['#' + str(b) for b in p['below']])} <- "
          f"{r['kind']}{' #' + str(r['number']) if 'number' in r else ''} ({r['branch']})")
    if p["children"]:
        print(f"  stacked on it: {', '.join('#' + str(c) for c in p['children'])}")
    head_by = who(a["head_commit"]) if a["head_commit_known"] else "unknown (not in local clone)"
    print(f"  signed: body {who(a['body'])}; earliest signed commit {who(a['earliest_signed_commit'])}; "
          f"head commit {head_by}")
    print(f"  recorded reviews:")
    for rv in p["reading"]["reviews"]:
        print(f"    - {rv['verdict']} round {rv.get('round') or '?'} {local(rv['at'])} at {rv.get('head')} "
              f"({rv['comment']}): {rv['summary']}")
    if not p["reading"]["reviews"]:
        print("    - none recorded")
    print("  commits (last 8):")
    for c in p["commits"][-8:]:
        by = "signer unknown" if c["signers"] is None else who(c["signers"][-1] if c["signers"] else None)
        print(f"    - {c['sha']} {by} {c['subject'][:80]}")
    print("  sessions that touched it (first touch first; pr-link records and PR URLs in transcripts):")
    for t in p["touched_by"]:
        role = f"linked {t['linked']}x" if t["linked"] else "mentioned"
        print(f"    - \"{t['title']}\" ({t['id'][:8]}) {role}, {t['mentions']} mentions, "
              f"{local(t['first'])} .. {local(t['last'])}; {t['status']}")
    for c in data["collisions"]:
        for mv in c["moves"]:
            dest = f" (added in {', '.join(mv['moved_to'])})" if mv["moved_to"] else ""
            print(f"  collision: #{mv['remover']} removes {mv['file']}:{mv['function']}{dest}; #{mv['editor']} edits it")
        for b in c["both_edit"]:
            other = c["b"] if c["a"] == p["number"] else c["a"]
            print(f"  collision: #{other} also edits {b['file']}:{b['function']}")
        if c["not_compared"]:
            print(f"  not compared with #{c['b'] if c['a'] == p['number'] else c['a']}: {', '.join(c['not_compared'])}")
    if data["shared_files_only"]:
        print("  file overlap only: " + ", ".join(f"#{s['other']} ({len(s['files'])})" for s in data["shared_files_only"]))
    for n in data["notes"]:
        print(f"  note: {n}")
    print_comments(p)


# ---------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", help="owner/name; default: the checkout's origin")
    ap.add_argument("--checkout", type=Path, default=DEFAULT_CHECKOUT)
    ap.add_argument("--json", action="store_true", help="print JSON instead of the digest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sw = sub.add_parser("sweep")
    sw.add_argument("--no-fetch", action="store_true")
    sw.add_argument("--no-worktrees", action="store_true")
    sw.add_argument("--no-owners", action="store_true", help="skip the transcript scan")
    sw.add_argument("--all-worktrees", action="store_true", help="also inspect detached worktrees")
    sw.add_argument("--closed-days", type=int, default=7)
    sw.add_argument("--no-save", action="store_true")
    pr = sub.add_parser("pr")
    pr.add_argument("number", type=int)
    pr.add_argument("--comments", choices=("unread", "all", "none"), default="unread")
    pr.add_argument("--no-fetch", action="store_true")
    pr.add_argument("--comments-only", action="store_true",
                    help="only the PR's facts and comments: no collisions, no session scan")
    rc = sub.add_parser("record")
    rc.add_argument("number", type=int)
    rc.add_argument("--comment", required=True, help="comment id as `pr` lists it (c123 or r456)")
    rc.add_argument("--kind", required=True, choices=KINDS)
    rc.add_argument("--summary", required=True, help="one line, in the model's words")
    rc.add_argument("--verdict", choices=VERDICTS)
    rc.add_argument("--round", type=int)
    rc.add_argument("--head", help="the head sha the review names")
    rc.add_argument("--net", help="the review's net-value call, as written")
    rc.add_argument("--asks-note-on", type=int, nargs="*", help="PRs the comment asks a note to be written on")
    sub.add_parser("diff")
    ss = sub.add_parser("sessions", help="each recent session's last owner messages and replies")
    ss.add_argument("--hours", type=float, default=6, help="closed sessions active within this window")
    ss.add_argument("--turns", type=int, default=2)
    ss.add_argument("--width", type=int, default=400, help="characters of each owner message; replies get 4x")
    ss.add_argument("only", nargs="*", help="session id prefixes")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "sessions":
            args.checkout = Path(args.checkout)
        elif not Path(args.checkout).is_dir():
            raise CouldNotRun(f"checkout not found: {args.checkout}")
        args.repo = args.repo or repo_of(args.checkout)
        if not args.repo:
            raise CouldNotRun(f"no GitHub origin remote in {args.checkout}; pass --repo owner/name")
        if args.cmd == "sweep":
            data = sweep(args)
            prior = snapshots()
            prev = json.loads(prior[-1].read_text(encoding="utf-8")) if prior else None
            changes = diff(prev, data) if prev else None
            if not args.no_save:
                data["snapshot"] = str(save_snapshot(data))
            if args.json:
                print(json.dumps({"changes": changes, **data}, ensure_ascii=False, indent=1))
            else:
                print_sweep(data, changes, local(prev["taken_at"]) if prev else None)
                if data.get("snapshot"):
                    print(f"\nSnapshot: {data['snapshot']}")
        elif args.cmd == "pr":
            data = pr_mode(args)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=1))
            else:
                print_pr(data)
        elif args.cmd == "sessions":
            data = sessions_mode(args)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=1))
            else:
                print_sessions(data, args.width)
        elif args.cmd == "record":
            print(json.dumps(record(args), ensure_ascii=False, indent=1))
        else:
            prior = snapshots()
            if len(prior) < 2:
                print("Fewer than two snapshots; run `sweep` first.")
                return 0
            old, new = (json.loads(p.read_text(encoding="utf-8")) for p in prior[-2:])
            print(f"Changes {local(old['taken_at'])} -> {local(new['taken_at'])}:")
            for c in diff(old, new) or ["none"]:
                print(f"  - {c}")
    except CouldNotRun as exc:
        print(f"COULD NOT RUN: {exc}", file=sys.stderr)
        return 2
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
