#!/usr/bin/env python3
"""Owner-dash matrix and re-look helpers.

CLI: dash_matrix.py put [PROJECT]   renumber, check score order, post the matrix
     dash_matrix.py relook N SECTION0_FILE BASE [PROJECT]
Import: from dash_matrix import Matrix, relook
"""
import re
import subprocess
import sys
from pathlib import Path

OWNER_DASH = str(Path.home() / "dev-skills/bin/owner-dash")
ROW = re.compile(r"\| \d+ \| \[#")
SCORE = re.compile(r"\| ([\d.]+) \([^|]*\) \|\s*$")
BOTTOM = "at the bottom (your call)"


def root(project):
    return Path.home() / ".local/share/owner-dash" / project


def repo(project):
    return (root(project) / "repo.txt").read_text().strip()


class Matrix:
    def __init__(self, project="datapack-agent"):
        self.project, self.path = project, root(project) / "matrix.md"
        self.lines = self.path.read_text().split("\n")

    def link(self, n):
        return f"[#{n}](https://github.com/{repo(self.project)}/pull/{n}/changes)"

    def rows(self):
        return [k for k, line in enumerate(self.lines) if ROW.match(line)]

    def pr(self, k):
        return re.search(r"\[#(\d+)\]", self.lines[k]).group(1)

    def drop(self, *numbers):
        keep = {str(n) for n in numbers}
        self.lines = [line for k, line in enumerate(self.lines) if not (ROW.match(line) and self.pr_of(line) in keep)]

    @staticmethod
    def pr_of(line):
        return re.search(r"\[#(\d+)\]", line).group(1)

    def renumber(self):
        for i, k in enumerate(self.rows(), 1):
            self.lines[k] = re.sub(r"^\| \d+ \|", f"| {i} |", self.lines[k])

    def order_problem(self):
        scored = [(i, float(m.group(1))) for i, k in enumerate(self.rows(), 1)
                  if (m := SCORE.search(self.lines[k])) and BOTTOM not in self.lines[k]]
        for (i, a), (j, b) in zip(scored, scored[1:]):
            if b > a:
                return f"order: row {j} scores {b} above row {i} at {a}"
        return ""

    def sort_by_score(self):
        rows = self.rows()
        unscored = [self.lines[k] for k in rows if not SCORE.search(self.lines[k])]
        scored = sorted((self.lines[k] for k in rows if SCORE.search(self.lines[k])),
                        key=lambda line: (BOTTOM in line, -float(SCORE.search(line).group(1))))
        for k, line in zip(rows, unscored + scored):
            self.lines[k] = line

    def put(self):
        self.renumber()
        problem = self.order_problem()
        if problem:
            return problem
        body = [line for line in self.lines if not line.startswith("*Posted")]
        self.path.write_text("\n".join(body))
        out = subprocess.run([OWNER_DASH, "--project", self.project, "put", "matrix", str(self.path)],
                             capture_output=True, text=True, timeout=60)
        self.lines = self.path.read_text().split("\n")
        return "" if out.returncode == 0 else out.stderr or out.stdout


def relook(number, section0, base, project="datapack-agent"):
    """Prepend section 0 to the posted walkthrough and re-post it with its attachments."""
    folder = root(project) / "walkthroughs"
    old = (folder / f"pr{number}.md").read_text().split("\n")
    old = old[1:] if old and old[0].startswith("*Posted") else old
    draft = Path(f"/tmp/relook{number}.md")
    draft.write_text(section0.rstrip() + "\n\n" + "\n".join(old))
    attach = sorted((folder / f"pr{number}.files").glob("*")) if (folder / f"pr{number}.files").is_dir() else []
    cmd = [OWNER_DASH, "--project", project, "--base", base, "put", "walkthrough", str(number), str(draft)]
    for f in attach:
        cmd += ["--attach", str(f)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return out.returncode, out.stdout + out.stderr


if __name__ == "__main__":
    if sys.argv[1] == "put":
        print(Matrix(*sys.argv[2:3]).put() or "posted")
    elif sys.argv[1] == "relook":
        print(relook(sys.argv[2], Path(sys.argv[3]).read_text(), sys.argv[4], *sys.argv[5:6]))
