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
import ast
import difflib
import html
import json
import re
import mimetypes
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote
import time
from concurrent.futures import ThreadPoolExecutor
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
    body = re.sub(r"\A\s*\*Posted [^*\n]*\*\s*", "", text)
    path.write_text(f"*Posted {stamp}*\n\n{body.strip()}\n", encoding="utf-8")
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
    pr = subprocess.run(["gh", "pr", "view", args.number, "-R", repo, "--json", "headRefOid,baseRefName",
                         "-q", ".headRefOid+\" \"+.baseRefName"], capture_output=True, text=True)
    if pr.returncode:
        return print(f"owner-dash: gh failed; no diff stored: {pr.stderr.strip()}", file=sys.stderr)
    sha, pr_base = pr.stdout.split()
    base = args.base or (stored and stored.group(1)) or pr_base
    compare = f"repos/{repo}/compare/{base}...{sha}"
    merge_base = subprocess.run(["gh", "api", compare, "--jq", ".merge_base_commit.sha"], capture_output=True, text=True)
    diff = subprocess.run(["gh", "api", compare, "-H", "Accept: application/vnd.github.diff"],
                          capture_output=True, text=True)
    if merge_base.returncode or diff.returncode:
        return print(f"owner-dash: gh failed; no diff stored: {(merge_base.stderr + diff.stderr).strip()}",
                     file=sys.stderr)
    path.write_text(f"# head {sha} base {base}\n{diff.stdout}", encoding="utf-8")
    names = touched_names(repo, merge_base.stdout.strip(), sha, parse_diff(diff.stdout))
    path.with_suffix(".names.json").write_text(json.dumps(names), encoding="utf-8")
    return True


def source(repo, ref, file):
    out = subprocess.run(["gh", "api", f"repos/{repo}/contents/{quote(file)}?ref={ref}", "-H",
                          "Accept: application/vnd.github.raw"], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def spans(text):
    """(first line, last line, name) for top-level functions, classes, their methods and module-level names."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    out = []
    def add(node, name):
        start = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])
        out.append((start, node.end_lineno, name))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add(node, node.name)
            for member in node.body if isinstance(node, ast.ClassDef) else []:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(member, member.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name):
                add(node, target.id)
    return out


def name_at(found, line):
    inside = [s for s in found if s[0] <= line <= s[1]]
    return max(inside)[2] if inside else None


def touched_names(repo, old_ref, new_ref, files):
    """{path: [names per hunk]}: the enclosing definition of every changed line, read from both versions."""
    py = [f for f in files if f["path"].endswith(".py")]
    with ThreadPoolExecutor(8) as pool:
        old = list(pool.map(lambda f: spans(source(repo, old_ref, f["old"])), py))
        new = list(pool.map(lambda f: spans(source(repo, new_ref, f["path"])), py))
    result = {}
    for f, old_spans, new_spans in zip(py, old, new):
        if old_spans is None or new_spans is None:
            continue
        per_hunk = []
        for hunk in f["hunks"]:
            a, c = (int(n) for n in re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", hunk["header"]).groups())
            names = set()
            for line in hunk["lines"]:
                kind, body = line[:1], line[1:]
                if kind == "-" and body.strip() and (n := name_at(old_spans, a)):
                    names.add(n)
                if kind == "+" and body.strip() and (n := name_at(new_spans, c)):
                    names.add(n)
                a += kind in " -"
                c += kind in " +"
            per_hunk.append(sorted(names))
        result[f["path"]] = per_hunk
    return result


def parse_diff(text):
    files, hunk = [], None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            old, new = re.match(r"diff --git a/(.*) b/(.*)", line).groups()
            files.append({"path": new, "old": old, "hunks": []})
            hunk = None
        elif line.startswith("@@") and files:
            hunk = {"header": line, "lines": []}
            files[-1]["hunks"].append(hunk)
        elif hunk is not None and line[:1] in ("+", "-", " ", "\\"):
            hunk["lines"].append(line)
    return files


def inline(old, new):
    """One line showing a mostly-unchanged line's edit: removed words struck, added words marked; None when the
    two lines share less than half their text, so they read better as a removed line and an added one."""
    a, b = re.findall(r"\w+|\s+|[^\w\s]", old[1:]), re.findall(r"\w+|\s+|[^\w\s]", new[1:])
    ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    kept = sum(len("".join(a[i1:i2])) for tag, i1, i2, _, _ in ops if tag == "equal")
    if kept * 2 < max(len(old), len(new)) - 1:
        return None
    out = ["~"]
    for tag, i1, i2, j1, j2 in ops:
        gone, added = html.escape("".join(a[i1:i2])), html.escape("".join(b[j1:j2]))
        out.append(gone if tag == "equal" else (gone and f"<del>{gone}</del>") + (added and f"<ins>{added}</ins>"))
    return "".join(out)


def diff_html(path, hunks):
    rows = []
    for hunk in hunks:
        rows.append(f'<span class=dh>{html.escape(hunk["header"])}</span>')
        lines, i = hunk["lines"], 0
        while i < len(lines):
            minus = i
            while minus < len(lines) and lines[minus][:1] == "-":
                minus += 1
            plus = minus
            while plus < len(lines) and lines[plus][:1] == "+":
                plus += 1
            if minus > i and plus > minus:
                olds, news, at = lines[i:minus], lines[minus:plus], 0
                for old in olds:
                    hit = next(((j, edit) for j in range(at, len(news)) if (edit := inline(old, news[j]))), None)
                    if not hit:
                        rows.append(f"<span class=dd>{html.escape(old)}</span>")
                        continue
                    j, edit = hit
                    rows += [f"<span class=da>{html.escape(l)}</span>" for l in news[at:j]]
                    rows.append(f"<span class=dm>{edit}</span>")
                    at = j + 1
                rows += [f"<span class=da>{html.escape(l)}</span>" for l in news[at:]]
                i = plus
                continue
            line = lines[i]
            rows.append(f'<span class={ {"+": "da", "-": "dd"}.get(line[:1], "dc") }>{html.escape(line)}</span>')
            i += 1
    return f'<div class=diff><div class=df>{html.escape(path)}</div><pre>{"".join(rows)}</pre></div>'


DEF = re.compile(r"\s*(?:async\s+)?(?:def|class)\s+(\w+)")


def touched(hunk):
    """Names whose lines this hunk changes: from the parsed sources when stored, else the diff's def lines."""
    if "names" in hunk:
        return set(hunk["names"])
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


def load_diff(path):
    raw = path.with_suffix(".diff").read_text(encoding="utf-8")
    files = parse_diff(raw)
    names_file = path.with_suffix(".names.json")
    names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.is_file() else {}
    for f in files:
        for hunk, found in zip(f["hunks"], names.get(f["path"], [])):
            hunk["names"] = found
    return raw, files


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
            if not re.search(rf"\b{re.escape(n)}\b", md if is_test(p) else how)]


def is_test(path):
    return any(part.startswith("test") for part in Path(path).parts)


def mentions(hunk, names):
    text = hunk["header"] + "\n" + "\n".join(l for l in hunk["lines"] if l[:1] in "+-")
    return bool(touched(hunk) & set(names)) or any(re.search(rf"\b{re.escape(n)}\b", text) for n in names)


def hunks_for(span, files):
    """(file, hunk, strength, by file) for a code span: strength 3 names the hunk's own definition, 2 a name its lines
    use, 1 only its file; by file when the span names the file."""
    parts = [p for p in re.split(r"[./:()\s,]+", span) if p]
    found = []
    for f in files:
        path = Path(f["path"])
        if path.stem in parts or f["path"].endswith(span.split(":")[0]):
            names = [p for p in parts if p not in (path.stem, path.suffix[1:]) and not p.isdigit()]
            found += [(f, h, strength(h, names), True) for h in f["hunks"] if not names or mentions(h, names)]
        elif len(parts) == 1 and re.fullmatch(r"[A-Za-z_]\w*", span) and (
                "_" in span or re.search(r"[a-z][A-Z]", span) or any(span in touched(h) for h in f["hunks"])):
            found += [(f, h, strength(h, parts), False) for h in f["hunks"] if mentions(h, parts)]
    return found


def strength(hunk, names):
    return 1 if not names else 3 if touched(hunk) & set(names) else 2


def changed(hunk):
    return sum(1 for l in hunk["lines"] if l[:1] in "+-")


def place(path):
    """Walkthrough tokens with each changed hunk shown once, after the paragraph that explains it best.

    Best: names the hunk's own definition over a name it only uses over its bare file; then the paragraph that also
    names the hunk's file; then the one in the section that owns it (4 for code, 8 for tests); then the paragraph
    naming the fewest hunks; then the first.
    """
    raw, files = load_diff(path)
    tokens = MARKDOWN.parse(path.read_text(encoding="utf-8"))
    claims, sections, current, number = {}, {}, [], None
    for i, token in enumerate(tokens):
        if token.type == "inline":
            number = m.group(1) if (m := re.match(r"\s*\*\*(\d+a?)\.", token.content)) else number
            current += [m for child in token.children or [] if child.type == "code_inline"
                        for m in hunks_for(child.content, files)]
        elif token.type == "paragraph_close" and current:
            claims[i], sections[i], current = current, number, []
    best = {}
    for i, found in claims.items():
        breadth, named = len({id(h) for _, h, _, _ in found}), {f["path"] for f, _, _, by_file in found if by_file}
        for f, h, s, _ in found:
            key = (-s, f["path"] not in named, sections[i] != ("8" if is_test(f["path"]) else "4"), breadth, i)
            if id(h) not in best or key < best[id(h)][0]:
                best[id(h)] = (key, i, f)
    out = []
    for i, token in enumerate(tokens):
        out.append(token)
        by_file = {}
        for f, h in {id(h): (f, h) for f, h, _, _ in claims.get(i, []) if best[id(h)][1] == i}.values():
            by_file.setdefault(f["path"], []).append(h)
        if not by_file:
            continue
        blocks = "".join(diff_html(p, hs) for p, hs in sorted(by_file.items(), key=lambda i: is_test(i[0])))
        block = Token("html_block", "", 0)
        block.content = (f"<details class=dx><summary>diff: {sum(changed(h) for hs in by_file.values() for h in hs)}"
                         f" lines</summary>{blocks}</details>")
        out.append(block)
    return out, files, set(best), raw.split("\n", 1)[0].removeprefix("# head ")[:8]


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


OPEN = {}


def open_prs(base):
    """The project's open PR numbers, asked of GitHub at most once a minute; None when unknown."""
    saved = base / "repo.txt"
    if not saved.is_file():
        return None
    asked, numbers = OPEN.get(base, (0, None))
    if time.time() - asked > 60:
        out = subprocess.run(["gh", "pr", "list", "-R", saved.read_text(encoding="utf-8").strip(), "--state", "open",
                              "--limit", "1000", "--json", "number", "--jq", ".[].number"], capture_output=True, text=True)
        numbers = None if out.returncode else set(out.stdout.split())
        OPEN[base] = (time.time(), numbers)
    return numbers


def with_walkthroughs(base, matrix_html):
    still_open, gone = open_prs(base), []

    def expand(row):
        found = re.search(r"#(\d+)", row.group(0))
        if found and still_open is not None and found.group(1) not in still_open:
            gone.append(f"#{found.group(1)}")
            return ""
        path = found and base / "walkthroughs" / f"pr{found.group(1)}.md"
        if not path or not path.is_file():
            return row.group(0)
        width = row.group(0).count("<td")
        return (f'{row.group(0)}<tr class=wt><td colspan={width}><details><summary>Walkthrough #{found.group(1)}'
                f'</summary>{render_walkthrough(path)}<button class=fold onclick="var d=this.closest(\'details\');'
                f'd.open=false;d.scrollIntoView({{block:\'nearest\'}})">Collapse walkthrough #{found.group(1)}'
                f'</button></details></td></tr>')
    head, sep, rest = matrix_html.partition("</table>")
    body = re.sub(r"<tr>\s*<td.*?</tr>", expand, head, flags=re.S) + sep + rest
    note = ("<p class=when>Could not ask GitHub which PRs are open; showing every row.</p>" if still_open is None
            else f"<p class=when>Hidden, merged or closed: {', '.join(gone)}</p>" if gone else "")
    return note + body


STYLE = """<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222}
a{color:#0b5cad}table{border-collapse:collapse;margin:12px 0}td,th{border:1px solid #ccc;padding:4px 8px;vertical-align:top}
th{background:#f3f3f3;text-align:left}pre{background:#f6f6f6;padding:10px;overflow-x:auto}code{background:#f3f3f3;padding:0 3px}
nav{margin-bottom:16px}nav a{margin-right:16px;font-weight:600}.when{color:#777;font-size:13px;margin-left:8px}
tr.wt>td{border-top:none;background:#fcfcfc}tr.wt summary{cursor:pointer;color:#0b5cad;font-size:13px}
button.fold{margin:12px 0 4px;padding:4px 12px;cursor:pointer}
details.dx{margin:4px 0 10px}details.dx>summary{cursor:pointer;color:#0b5cad;font-size:13px}
.diff{border:1px solid #ddd;margin:6px 0}.df{background:#eef;padding:2px 8px;font:13px monospace}
.diff pre{margin:0;padding:4px 0;font-size:12.5px;background:#fff}
.diff pre span{display:block;padding:0 8px;white-space:pre-wrap;overflow-wrap:anywhere}
.da{background:#e6ffec}.dd{background:#ffebe9}.dh{color:#777;background:#f6f8fa}
.dm{background:#fffbe6}.dm del{background:#ffc1bd;color:#86181d}.dm ins{background:#abf2bc;text-decoration:none}
.ok{color:#1a7f37}.bad{color:#b42318;font-weight:600}
::highlight(same){background:#ffe58a}
</style>"""

SAME_WORD = """<script>
document.addEventListener("selectionchange", () => {
  if (!window.CSS || !CSS.highlights) return;
  CSS.highlights.delete("same");
  const word = String(getSelection()).trim();
  if (!/^\\w{2,}$/.test(word)) return;
  const find = new RegExp(`(?<!\\\\w)${word}(?!\\\\w)`, "g"), ranges = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node; (node = walk.nextNode()) && ranges.length < 5000;) {
    for (const m of node.data.matchAll(find)) {
      const r = new Range(); r.setStart(node, m.index); r.setEnd(node, m.index + word.length); ranges.push(r);
    }
  }
  CSS.highlights.set("same", new Highlight(...ranges));
});
</script>"""


def page(project, title, body):
    nav = "".join(f'<a href="/{p.name}/">{html.escape(p.name)}</a>' for p in sorted(ROOT.glob("*/")) if p.is_dir())
    sub = "" if not project else (f'<nav><a href="/{project}/">Matrix</a><a href="/{project}/walkthroughs/">Walkthroughs</a>'
                                  f'<a href="/{project}/reviews/">Reviews</a></nav>')
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>{STYLE}<nav>{nav}</nav>{sub}{body}"
            f"{SAME_WORD}")


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
            still_open = open_prs(base)

            def items(chosen):
                return "".join(
                    f'<li><a href="/{project}/{parts[1]}/{f.name}">#{pr_number(f)}</a>'
                    f'<span class=when>{time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime))}</span></li>'
                    for f in chosen)
            done = [f for f in files if still_open is not None and str(pr_number(f)) not in still_open]
            body = f"<h1>{parts[1].title()}</h1><ul>{items([f for f in files if f not in done])}</ul>"
            if done:
                body += f"<details class=dx><summary>Merged or closed ({len(done)})</summary><ul>{items(done)}</ul></details>"
            return self.send(page(project, parts[1], body))
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
