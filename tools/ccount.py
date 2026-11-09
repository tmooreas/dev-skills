import ast
import io
import subprocess
import sys
import tokenize


def show(rev, path):
    p = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def count(src):
    if not src:
        return 0, 0
    comments = sum(1 for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.COMMENT)
    doc = 0
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                doc += first.end_lineno - first.lineno + 1
    return comments, doc


base, head = sys.argv[1], sys.argv[2]
files = subprocess.run(["git", "diff", "--name-only", base, head], capture_output=True, text=True).stdout.split()
tot = [0, 0, 0, 0]
for f in files:
    if not f.endswith(".py"):
        continue
    (c0, d0), (c1, d1) = count(show(base, f)), count(show(head, f))
    tot = [tot[0] + c0, tot[1] + c1, tot[2] + d0, tot[3] + d1]
    print(f"{f}: comments {c0}->{c1}, docstring lines {d0}->{d1}")
print(f"TOTAL comments {tot[0]} -> {tot[1]}  docstring lines {tot[2]} -> {tot[3]}  net {tot[1] - tot[0] + tot[3] - tot[2]}")
