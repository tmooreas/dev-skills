#!/usr/bin/env python3
"""Owner dashboard: a local web page of PR walkthroughs, reviews and the PR matrix.

    owner-dash --repo owner/name put walkthrough 362 file.md     (or - for stdin; also stores the PR's diff)
    owner-dash put review 362 file.md
    owner-dash put matrix file.md
    owner-dash show walkthrough 362
    owner-dash serve [--port 8765]

Every command takes --project NAME (default: the current git repo's name).
Files live in $OWNER_DASH_DIR (default ~/.local/share/owner-dash)/<project>/.
Run through bin/owner-dash, which provides its venv.
"""
import argparse
import html
import re
import mimetypes
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from os import environ
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

ROOT = Path(environ.get("OWNER_DASH_DIR", Path.home() / ".local/share/owner-dash"))
MARKDOWN = MarkdownIt("commonmark", {"linkify": False}).enable("table")
KINDS = ("walkthrough", "review")


def default_project():
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(out.stdout.strip()).name if out.returncode == 0 else "default"


def target(project, kind, number=None):
    base = ROOT / project
    return base / "matrix.md" if kind == "matrix" else base / f"{kind}s" / f"pr{number}.md"


def put(args):
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    path = target(args.project, args.kind, args.number)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    path.write_text(f"*Posted {stamp}*\n\n{text.strip()}\n", encoding="utf-8")
    print(path)
    if args.kind == "walkthrough":
        folder = path.with_suffix(".files")
        for item in args.attach:
            folder.mkdir(exist_ok=True)
            shutil.copy2(item, folder / Path(item).name)
        if fetch_diff(args):
            missing, report = coverage_report(path)
            print(report, file=sys.stderr)
            if missing:
                sys.exit(1)


def show(args):
    path = target(args.project, args.kind, args.number)
    if not path.is_file():
        sys.exit(f"owner-dash: nothing posted at {path}")
    print(path.read_text(encoding="utf-8"))


def render(md):
    return MARKDOWN.render(md)


def repo_of(args):
    saved = ROOT / args.project / "repo.txt"
    if args.repo:
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(args.repo, encoding="utf-8")
        return args.repo
    if saved.is_file():
        return saved.read_text(encoding="utf-8").strip()
    out = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                         capture_output=True, text=True)
    return out.stdout.strip() or None


def fetch_diff(args):
    repo = repo_of(args)
    if not repo:
        return print("owner-dash: no --repo and none known; walkthrough posted without its diff", file=sys.stderr)
    path = target(args.project, "walkthrough", args.number).with_suffix(".diff")
    stored = re.search(r" base (\S+)", path.read_text(encoding="utf-8").split("\n", 1)[0]) if path.is_file() else None
    base = args.base or (stored and stored.group(1))
    head = subprocess.run(["gh", "pr", "view", args.number, "-R", repo, "--json", "headRefOid", "-q", ".headRefOid"],
                          capture_output=True, text=True)
    sha = head.stdout.strip()
    if base:
        diff = subprocess.run(["gh", "api", f"repos/{repo}/compare/{base}...{sha}", "-H",
                               "Accept: application/vnd.github.diff"], capture_output=True, text=True)
    else:
        diff = subprocess.run(["gh", "pr", "diff", args.number, "-R", repo], capture_output=True, text=True)
    if head.returncode or diff.returncode:
        return print(f"owner-dash: gh failed; no diff stored: {(head.stderr + diff.stderr).strip()}", file=sys.stderr)
    path.write_text(f"# head {sha}{f' base {base}' if base else ''}\n{diff.stdout}", encoding="utf-8")
    return True


def parse_diff(text):
    files, hunk = [], None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            files.append({"path": line.split(" b/", 1)[-1], "hunks": []})
            hunk = None
        elif line.startswith("@@") and files:
            hunk = {"header": line, "lines": []}
            files[-1]["hunks"].append(hunk)
        elif hunk is not None and line[:1] in ("+", "-", " ", "\\"):
            hunk["lines"].append(line)
    return files


def diff_html(path, hunks):
    rows = []
    for hunk in hunks:
        rows.append(f'<span class=dh>{html.escape(hunk["header"])}</span>')
        for line in hunk["lines"]:
            cls = {"+": "da", "-": "dd"}.get(line[:1], "dc")
            rows.append(f"<span class={cls}>{html.escape(line)}</span>")
    return f'<div class=diff><div class=df>{html.escape(path)}</div><pre>{"".join(rows)}</pre></div>'


DEF = re.compile(r"\s*(?:async\s+)?(?:def|class)\s+(\w+)")


def touched(hunk):
    """Functions, classes and module-level names whose lines this hunk changes (Python files)."""
    context = hunk["header"].split("@@")[-1]
    current = (DEF.match(context) or [None, None])[1]
    names = set()
    for line in hunk["lines"]:
        body = line[1:]
        if m := DEF.match(body):
            current = m.group(1)
        elif body[:1] not in ("", " ", "\t", "#", "@", ")", "]", "}"):
            current = (re.match(r"(\w+)\s*[:=]", body) or [None, None])[1]
        if line[:1] in "+-" and body.strip() and current:
            names.add(current)
    return names


def units(files):
    """(path, name) for every touched name in a .py file."""
    return sorted({(f["path"], n) for f in files if f["path"].endswith(".py") for h in f["hunks"] for n in touched(h)})


def section(md, number):
    m = re.search(rf"^\W*{number}\.(.*?)(?=^\W*{number + 1}\.|\Z)", md, re.S | re.M)
    return m.group(0) if m else ""


def unnamed(md, files):
    """Touched names not mentioned where they must be: code in section 4, tests anywhere."""
    how = section(md, 4)
    return [(p, n) for p, n in units(files)
            if not re.search(rf"\b{re.escape(n)}\b", md if "test" in Path(p).name else how)]


def mentions(hunk, names):
    text = hunk["header"] + "\n" + "\n".join(l for l in hunk["lines"] if l[:1] in "+-")
    return bool(touched(hunk) & set(names)) or any(re.search(rf"\b{re.escape(n)}\b", text) for n in names)


def hunks_for(span, files):
    """(file, hunk, specific) for a code span: a file (not specific), a file's symbol, or one identifier."""
    parts = [p for p in re.split(r"[./:()\s,]+", span) if p]
    found = []
    for f in files:
        path = Path(f["path"])
        if path.stem in parts or f["path"].endswith(span.split(":")[0]):
            names = [p for p in parts if p not in (path.stem, path.suffix[1:]) and not p.isdigit()]
            found += [(f, h, bool(names)) for h in f["hunks"] if not names or mentions(h, names)]
        elif len(parts) == 1 and re.fullmatch(r"[A-Za-z_]\w*", span) and (
                "_" in span or re.search(r"[a-z][A-Z]", span) or any(span in touched(h) for h in f["hunks"])):
            found += [(f, h, True) for h in f["hunks"] if mentions(h, parts)]
    return found


def changed(hunk):
    return sum(1 for l in hunk["lines"] if l[:1] in "+-")


def place(path):
    """Walkthrough tokens with each referenced hunk inserted after the paragraph that names it.

    A file named on its own takes only the hunks no symbol mention anywhere claims.
    """
    raw = path.with_suffix(".diff").read_text(encoding="utf-8")
    files = parse_diff(raw)
    tokens = MARKDOWN.parse(path.read_text(encoding="utf-8"))
    claims, current = {}, []
    for i, token in enumerate(tokens):
        if token.type == "inline":
            current += [m for child in token.children or [] if child.type == "code_inline"
                        for m in hunks_for(child.content, files)]
        elif token.type == "paragraph_close" and current:
            claims[i], current = current, []
    specific = {id(h) for found in claims.values() for _, h, s in found if s}
    out, placed = [], set()
    for i, token in enumerate(tokens):
        out.append(token)
        by_file, seen = {}, set()
        for f, h, s in claims.get(i, []):
            if id(h) in seen or (not s and id(h) in specific):
                continue
            seen.add(id(h))
            by_file.setdefault(f["path"], []).append(h)
        if not by_file:
            continue
        placed |= seen
        blocks = "".join(diff_html(p, hs) for p, hs in sorted(by_file.items(), key=lambda i: "test" in i[0]))
        block = Token("html_block", "", 0)
        block.content = (f"<details class=dx><summary>diff: {sum(changed(h) for hs in by_file.values() for h in hs)}"
                         f" lines</summary>{blocks}</details>")
        out.append(block)
    return out, files, placed, raw.split("\n", 1)[0].removeprefix("# head ")[:8]


def unplaced(files, placed):
    return [(f, h) for f in files for h in f["hunks"] if id(h) not in placed and changed(h)]


def coverage(path, files, placed):
    """Deterministic checks: every changed line is shown beside the text; every touched name is named."""
    total = sum(changed(h) for f in files for h in f["hunks"])
    missing = unplaced(files, placed)
    names = unnamed(path.read_text(encoding="utf-8"), files)
    return total, missing, names


def coverage_report(path):
    _, files, placed, head = place(path)
    total, missing, names = coverage(path, files, placed)
    lines = [f"owner-dash: diff at {head}: {total - sum(changed(h) for _, h in missing)}/{total} changed lines placed; "
             f"{len(names)} touched names not mentioned (code: section 4; tests: anywhere)"]
    lines += [f"  not placed: {f['path']} {h['header'].split('@@')[1].strip()} ({changed(h)} lines)" for f, h in missing]
    lines += [f"  not mentioned: {p} {n}" for p, n in names]
    return bool(missing or names), "\n".join(lines)


def attachments(path):
    folder = path.with_suffix(".files")
    return sorted(folder.iterdir()) if folder.is_dir() else []


def render_walkthrough(path):
    files_html = "".join(f'<li><a href="{file_url(a)}">{html.escape(a.name)}</a></li>' for a in attachments(path))
    files_html = f"<p><strong>Files:</strong></p><ul>{files_html}</ul>" if files_html else ""
    if not path.with_suffix(".diff").is_file():
        return files_html + link_files(path, render(path.read_text(encoding="utf-8")))
    out, files, placed, head = place(path)
    total, missing, names = coverage(path, files, placed)
    shown = total - sum(changed(h) for _, h in missing)
    status = (f'<p class={"ok" if not missing else "bad"}>Diff at {html.escape(head)}: {shown} of {total} changed lines '
              f'shown next to the text.</p>')
    if names:
        status += ("<p class=bad>Touched but not mentioned (code in section 4, tests anywhere): "
                   + ", ".join(f"<code>{html.escape(n)}</code> ({html.escape(Path(p).name)})" for p, n in names) + "</p>")
    tail = Token("html_block", "", 0)
    tail.content = ""
    if missing:
        by_file = {}
        for f, h in missing:
            by_file.setdefault(f["path"], []).append(h)
        tail.content += "<h3 class=bad>Not placed in the text</h3>" + "".join(diff_html(p, hs) for p, hs in by_file.items())
    tail.content += (f"<h3>Full diff at {html.escape(head)}</h3>"
                     + "".join(f"<details class=dx><summary>{html.escape(f['path'])}</summary>"
                               f"{diff_html(f['path'], f['hunks'])}</details>" for f in files))
    out.append(tail)
    return status + files_html + link_files(path, MARKDOWN.renderer.render(out, MARKDOWN.options, {}))


def file_url(item):
    project = item.parent.parent.parent.name
    return f"/{project}/walkthroughs/{quote(item.parent.name)}/{quote(item.name)}"


def link_files(path, body):
    for a in attachments(path):
        body = body.replace(f'href="{a.name}"', f'href="{file_url(a)}"')
    return body


def with_walkthroughs(base, matrix_html):
    def expand(row):
        found = re.search(r"#(\d+)", row.group(0))
        path = found and base / "walkthroughs" / f"pr{found.group(1)}.md"
        if not path or not path.is_file():
            return row.group(0)
        width = row.group(0).count("<td")
        return (f'{row.group(0)}<tr class=wt><td colspan={width}><details><summary>Walkthrough #{found.group(1)}'
                f'</summary>{render_walkthrough(path)}</details></td></tr>')
    head, sep, rest = matrix_html.partition("</table>")
    return re.sub(r"<tr>\s*<td.*?</tr>", expand, head, flags=re.S) + sep + rest


STYLE = """<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222}
a{color:#0b5cad}table{border-collapse:collapse;margin:12px 0}td,th{border:1px solid #ccc;padding:4px 8px;vertical-align:top}
th{background:#f3f3f3;text-align:left}pre{background:#f6f6f6;padding:10px;overflow-x:auto}code{background:#f3f3f3;padding:0 3px}
nav{margin-bottom:16px}nav a{margin-right:16px;font-weight:600}.when{color:#777;font-size:13px;margin-left:8px}
tr.wt>td{border-top:none;background:#fcfcfc}tr.wt summary{cursor:pointer;color:#0b5cad;font-size:13px}
details.dx{margin:4px 0 10px}details.dx>summary{cursor:pointer;color:#0b5cad;font-size:13px}
.diff{border:1px solid #ddd;margin:6px 0}.df{background:#eef;padding:2px 8px;font:13px monospace}
.diff pre{margin:0;padding:4px 0;font-size:12.5px;background:#fff}.diff pre span{display:block;padding:0 8px;white-space:pre}
.da{background:#e6ffec}.dd{background:#ffebe9}.dh{color:#777;background:#f6f8fa}
.ok{color:#1a7f37}.bad{color:#b42318;font-weight:600}
</style>"""


def page(project, title, body):
    nav = "".join(f'<a href="/{p.name}/">{html.escape(p.name)}</a>' for p in sorted(ROOT.glob("*/")) if p.is_dir())
    sub = "" if not project else (f'<nav><a href="/{project}/">Matrix</a><a href="/{project}/walkthroughs/">Walkthroughs</a>'
                                  f'<a href="/{project}/reviews/">Reviews</a></nav>')
    return f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>{STYLE}<nav>{nav}</nav>{sub}{body}"


def pr_number(path):
    return int(re.sub(r"\D", "", path.stem) or 0)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parts = [unquote(p) for p in self.path.split("?")[0].split("/")]
        parts = [p for p in parts if p and p not in (".", "..") and "/" not in p]
        if not parts:
            return self.send(page(None, "owner-dash", "<p>Pick a project above.</p>"))
        project = parts[0]
        base = ROOT / project
        if not base.is_dir():
            return self.send(page(None, "Not found", "<p>No such project.</p>"), 404)
        if len(parts) == 1:
            matrix = base / "matrix.md"
            body = with_walkthroughs(base, render(matrix.read_text(encoding="utf-8"))) if matrix.is_file() \
                else "<p>No matrix posted.</p>"
            return self.send(page(project, f"{project} matrix", body))
        folder = base / parts[1]
        if parts[1] not in (f"{k}s" for k in KINDS):
            return self.send(page(project, "Not found", "<p>Not found.</p>"), 404)
        if len(parts) == 2:
            files = sorted(folder.glob("pr*.md"), key=pr_number, reverse=True) if folder.is_dir() else []
            items = "".join(
                f'<li><a href="/{project}/{parts[1]}/{f.name}">#{pr_number(f)}</a>'
                f'<span class=when>{time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime))}</span></li>'
                for f in files)
            return self.send(page(project, parts[1], f"<h1>{parts[1].title()}</h1><ul>{items}</ul>"))
        if len(parts) == 4 and parts[1] == "walkthroughs" and parts[2].endswith(".files"):
            return self.send_file(folder / parts[2] / parts[3])
        path = folder / parts[2]
        if len(parts) != 3 or not path.is_file():
            return self.send(page(project, "Not found", "<p>Not found.</p>"), 404)
        body = render_walkthrough(path) if parts[1] == "walkthroughs" else render(path.read_text(encoding="utf-8"))
        self.send(page(project, path.stem, body))

    def send(self, body, status=200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path):
        if not path.is_file():
            return self.send(page(None, "Not found", "<p>Not found.</p>"), 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def serve(args):
    ROOT.mkdir(parents=True, exist_ok=True)
    print(f"owner-dash: serving {ROOT} on http://localhost:{args.port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(prog="owner-dash")
    parser.add_argument("--project", default=None)
    parser.add_argument("--repo", default=None, help="GitHub owner/name for diffs; remembered per project")
    parser.add_argument("--base", default=None,
                        help="diff a stacked PR against this branch, not main; remembered per walkthrough")
    sub = parser.add_subparsers(dest="command", required=True)
    p_put = sub.add_parser("put")
    p_show = sub.add_parser("show")
    for p in (p_put, p_show):
        p.add_argument("kind", choices=(*KINDS, "matrix"))
        p.add_argument("rest", nargs="*")
    sub.add_parser("serve").add_argument("--port", type=int, default=8765)
    p_put.add_argument("--attach", action="append", default=[], help="file shown with a walkthrough (repeatable)")
    p_show.set_defaults(attach=[])
    sub.add_parser("check", help="refetch a posted walkthrough's diff and run its checks").add_argument("n")
    args = parser.parse_args()
    args.project = args.project or default_project()
    if args.command == "serve":
        return serve(args)
    if args.command == "check":
        args.number = args.n
        path = target(args.project, "walkthrough", args.n)
        if not path.is_file():
            sys.exit(f"owner-dash: nothing posted at {path}")
        if not fetch_diff(args):
            sys.exit(1)
        failed, report = coverage_report(path)
        print(report)
        sys.exit(1 if failed else 0)
    need = (1 if args.kind == "matrix" else 2) - (args.command == "show")
    if len(args.rest) != need:
        parser.error(f"{args.command} {args.kind} takes {'<N> ' if args.kind != 'matrix' else ''}"
                     f"{'<file|->' if args.command == 'put' else ''}".strip())
    args.number = None if args.kind == "matrix" else args.rest[0]
    args.file = args.rest[-1] if args.command == "put" else None
    (put if args.command == "put" else show)(args)


if __name__ == "__main__":
    main()
