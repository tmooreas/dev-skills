#!/usr/bin/env python3
"""Owner dashboard: a local web page of PR walkthroughs, reviews and the PR matrix.

    owner-dash put walkthrough 362 file.md     (or - for stdin)
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
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from os import environ
from pathlib import Path

from markdown_it import MarkdownIt

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


def show(args):
    path = target(args.project, args.kind, args.number)
    if not path.is_file():
        sys.exit(f"owner-dash: nothing posted at {path}")
    print(path.read_text(encoding="utf-8"))


def render(md):
    return MARKDOWN.render(md)


STYLE = """<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222}
a{color:#0b5cad}table{border-collapse:collapse;margin:12px 0}td,th{border:1px solid #ccc;padding:4px 8px;vertical-align:top}
th{background:#f3f3f3;text-align:left}pre{background:#f6f6f6;padding:10px;overflow-x:auto}code{background:#f3f3f3;padding:0 3px}
nav{margin-bottom:16px}nav a{margin-right:16px;font-weight:600}.when{color:#777;font-size:13px;margin-left:8px}
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
        parts = [p for p in self.path.split("?")[0].split("/") if p and p not in (".", "..")]
        if not parts:
            return self.send(page(None, "owner-dash", "<p>Pick a project above.</p>"))
        project = parts[0]
        base = ROOT / project
        if not base.is_dir():
            return self.send(page(None, "Not found", "<p>No such project.</p>"), 404)
        if len(parts) == 1:
            matrix = base / "matrix.md"
            body = render(matrix.read_text(encoding="utf-8")) if matrix.is_file() else "<p>No matrix posted.</p>"
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
        path = folder / parts[2]
        if not path.is_file():
            return self.send(page(project, "Not found", "<p>Not found.</p>"), 404)
        self.send(page(project, path.stem, render(path.read_text(encoding="utf-8"))))

    def send(self, body, status=200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
    sub = parser.add_subparsers(dest="command", required=True)
    p_put = sub.add_parser("put")
    p_show = sub.add_parser("show")
    for p in (p_put, p_show):
        p.add_argument("kind", choices=(*KINDS, "matrix"))
        p.add_argument("rest", nargs="*")
    sub.add_parser("serve").add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    args.project = args.project or default_project()
    if args.command == "serve":
        return serve(args)
    need = (1 if args.kind == "matrix" else 2) - (args.command == "show")
    if len(args.rest) != need:
        parser.error(f"{args.command} {args.kind} takes {'<N> ' if args.kind != 'matrix' else ''}"
                     f"{'<file|->' if args.command == 'put' else ''}".strip())
    args.number = None if args.kind == "matrix" else args.rest[0]
    args.file = args.rest[-1] if args.command == "put" else None
    (put if args.command == "put" else show)(args)


if __name__ == "__main__":
    main()
