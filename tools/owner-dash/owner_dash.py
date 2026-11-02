#!/usr/bin/env python3
"""Owner dashboard: a local web page of PR walkthroughs, reviews and the PR matrix.

    owner-dash --repo owner/name put walkthrough 362 file.md     (or - for stdin; also stores the PR's diff)
    owner-dash put review 362 file.md
    owner-dash put brief settings-match file.md                  (a brief awaiting or past owner approval, by name)
    owner-dash put matrix file.md
    owner-dash show walkthrough 362
    owner-dash approvals                                          (each PR's and brief's latest decision, and whether it is current)
    owner-dash serve [--port 8765]

Every command takes --project NAME (default: the current git repo's name).
Files live in $OWNER_DASH_DIR (default ~/.local/share/owner-dash)/<project>/.
Run through bin/owner-dash, which provides its venv.
"""
import argparse
import ast
import hashlib
import difflib
import html
import json
import re
import mimetypes
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, quote, unquote
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from os import environ
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pygments.lexers import PythonLexer
from pygments.token import STANDARD_TYPES

ROOT = Path(environ.get("OWNER_DASH_DIR", Path.home() / ".local/share/owner-dash"))
MARKDOWN = MarkdownIt("commonmark", {"linkify": False}).enable("table")
KINDS = ("walkthrough", "review", "brief", "report")
BRIEF_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def default_project():
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(out.stdout.strip()).name if out.returncode == 0 else "default"


def target(project, kind, number=None):
    base = ROOT / project
    if kind == "matrix":
        return base / "matrix.md"
    if kind in ("brief", "report"):
        if not BRIEF_NAME.fullmatch(number):
            sys.exit(f"owner-dash: a {kind}'s name is lowercase letters, digits and dashes, not {number!r}")
        return base / f"{kind}s" / f"{number}.md"
    return base / f"{kind}s" / f"pr{number}.md"


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
            if Path(item).resolve() != (folder / Path(item).name).resolve():
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
            names = []
            for line in hunk["lines"]:
                kind = line[:1]
                names.append(name_at(old_spans, a) if kind == "-" else name_at(new_spans, c) if kind in " +" else None)
                a += kind in " -"
                c += kind in " +"
            per_hunk.append(names)
        spans_of = {}
        for first, last, name in new_spans:
            spans_of.setdefault(name, []).append([first, last])
        result[f["path"]] = {"hunks": per_hunk, "spans": spans_of}
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


def python_lines(lines):
    """Each diff line's text as syntax-coloured html. The old side (context and removed lines) and the new side
    (context and added lines) are each lexed whole, so a docstring spanning lines colours right."""
    shown = [html.escape(l[1:]) for l in lines]
    for side in "-+":
        picked = [k for k, l in enumerate(lines) if l[:1] in (" ", side)]
        out, line = [], []
        for kind, value in PythonLexer(stripnl=False, ensurenl=False).get_tokens("\n".join(lines[k][1:] for k in picked)):
            css = STANDARD_TYPES.get(kind) or STANDARD_TYPES.get(kind.parent, "")
            for n, part in enumerate(value.split("\n")):
                if n:
                    out.append("".join(line))
                    line = []
                if part:
                    line.append(f"<i class=y{css}>{html.escape(part)}</i>" if css else html.escape(part))
        out.append("".join(line))
        for k, text in zip(picked, out):
            if lines[k][:1] == side or side == "+":
                shown[k] = text
    return shown


def diff_html(path, hunks):
    """Prose (.md) shows an edited line once with its changed words marked; code keeps plain removed/added lines,
    syntax-coloured for Python."""
    rows, prose = [], path.endswith(".md")
    for hunk in hunks:
        rows.append(f'<span class=dh>{html.escape(hunk["header"])}</span>')
        lines, i = hunk["lines"], 0
        code = python_lines(lines) if path.endswith(".py") else [html.escape(l[1:]) for l in lines]
        while i < len(lines):
            minus = i
            while minus < len(lines) and lines[minus][:1] == "-":
                minus += 1
            plus = minus
            while plus < len(lines) and lines[plus][:1] == "+":
                plus += 1
            if prose and minus > i and plus > minus:
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
            rows.append(f'<span class={ {"+": "da", "-": "dd"}.get(line[:1], "dc") }>{html.escape(line[:1])}{code[i]}</span>')
            i += 1
    return f'<div class=diff><div class=df>{html.escape(path)}</div><pre>{"".join(rows)}</pre></div>'


DEF = re.compile(r"\s*(?:async\s+)?(?:def|class)\s+(\w+)")


def line_names(path, hunk):
    if "defs" in hunk:
        return hunk["defs"]
    if not path.endswith(".py"):
        return [None] * len(hunk["lines"])
    current = (DEF.match(hunk["header"].split("@@")[-1]) or [None, None])[1]
    names = []
    for line in hunk["lines"]:
        body = line[1:]
        if m := DEF.match(body):
            current = m.group(1)
        elif body[:1] not in ("", " ", "\t", "#", "@", ")", "]", "}"):
            current = (re.match(r"(\w+)\s*[:=]", body) or [None, None])[1]
        names.append(current)
    return names


def pieces(path, hunk):
    lines, names = hunk["lines"], line_names(path, hunk)
    c = int(re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", hunk["header"]).group(1))
    at, runs, run = [], [], []
    for k, line in enumerate(lines):
        at.append(c)
        c += line[:1] in " +"
        if line[:1] != " ":
            run.append(k)
        elif run:
            runs, run = runs + [run], []
    parts, new_side = [], {n for n, l in zip(names, lines) if l[:1] in " +"}
    for run in runs + [run]:
        real = [k for k in run if lines[k][:1] in "+-" and lines[k][1:].strip()]
        name = names[real[0]] if real else None
        for k in run:
            name = names[k] if k in real else name
            if lines[k][:1] not in "+-":
                continue
            if parts and parts[-1][0][-1] == name and k - parts[-1][2] <= 5:
                parts[-1][2] = k
            elif (parts and parts[-1][2] == k - 1 and lines[k - 1][:1] + lines[k][:1] == "-+"
                  and parts[-1][0][-1] and parts[-1][0][-1] not in new_side):
                parts[-1][0].append(name)
                parts[-1][2] = k
            else:
                parts.append([[name], k, k])
    out = []
    for n, (owners, first, last) in enumerate(parts):
        lo = parts[n - 1][2] + 1 if n else 0
        hi = parts[n + 1][1] - 1 if n + 1 < len(parts) else len(lines) - 1
        start, end = first, last
        while start > max(lo, first - 2) and lines[start - 1][:1] == " " and names[start - 1] in owners:
            start -= 1
        while end < hi and (lines[end + 1][:1] == "\\" or end < last + 2 and lines[end + 1][:1] == " "
                            and names[end + 1] in owners):
            end += 1
        owners = [o for o in owners if o]
        real = [at[k] for k in range(first, last + 1) if lines[k][:1] in "+-" and lines[k][1:].strip()] or [at[first]]
        out.append({"header": " ".join([f"{Path(path).name}:{at[start]}", ", ".join(owners)]).strip(),
                    "lines": lines[start:end + 1], "names": owners, "span": (real[0], real[-1])})
    return out


def touched(piece):
    return set(piece["names"])


def load_diff(path):
    raw = path.with_suffix(".diff").read_text(encoding="utf-8")
    files = parse_diff(raw)
    names_file = path.with_suffix(".names.json")
    names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.is_file() else {}
    for f in files:
        stored = names.get(f["path"], {})
        for hunk, found in zip(f["hunks"], stored.get("hunks", [])):
            hunk["defs"] = found
        f["spans"] = stored.get("spans")
        f["pieces"] = [p for h in f["hunks"] for p in pieces(f["path"], h)]
        for p in f["pieces"]:
            p["outer"] = sorted({n for n, found in (f["spans"] or {}).items() for first, last in found
                                 if first <= p["span"][0] and p["span"][1] <= last} - touched(p))
    return raw, files


def units(files):
    """(path, name) for every touched name in a .py file."""
    return sorted({(f["path"], n) for f in files if f["path"].endswith(".py") for h in f["pieces"] for n in touched(h)})


def section(md, number):
    """A walkthrough section: from its bold heading (`**4. ...` or `4. **...`) to the next one's. A plain numbered
    list inside another section is not a heading."""
    head = lambda n: rf"^(?:\*\*{n}\.|{n}\.\s+\*\*)"
    m = re.search(rf"{head(number)}.*?(?={head(number + 1)}|\Z)", md, re.S | re.M)
    return m.group(0) if m else ""


def unnamed(md, files):
    """Touched names not mentioned where they must be: code in section 4, tests anywhere."""
    how = section(md, 4)
    return [(p, n) for p, n in units(files)
            if not re.search(rf"\b{re.escape(n)}\b", md if is_test(p) else how)]


def is_test(path):
    return any(part.startswith("test") for part in Path(path).parts)


def mentions(hunk, names):
    text = "\n".join(l for l in hunk["lines"] if l[:1] in "+-")
    return bool((touched(hunk) | set(hunk["outer"])) & set(names)) or any(re.search(rf"\b{re.escape(n)}\b", text)
                                                                        for n in names)


def hunks_for(span, files):
    span = re.sub(r"^([\w.]+)\(.*\)$", r"\1", span)
    parts = [p for p in re.split(r"[./:()\s,]+", span) if p]
    method = re.fullmatch(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)", span)
    head = span.split(":")[0]
    found = []
    for f in files:
        path = Path(f["path"])
        if ("/" + f["path"]).endswith("/" + head) if "/" in head else path.stem in parts:
            names = [p for p in parts if p not in path.parts and p not in (path.stem, path.suffix[1:]) and not p.isdigit()]
            found += [(f, h, strength(h, names), True) for h in f["pieces"] if not names or mentions(h, names)]
        elif method:
            found += [(f, h, 4, False) for h in f["pieces"]
                      if method.group(2) in touched(h) and (f["spans"] is None or method.group(1) in h["outer"])]
        elif len(parts) == 1 and re.fullmatch(r"[A-Za-z_]\w*", span) and (
                "_" in span or re.search(r"[a-z][A-Z]", span) or any(span in touched(h) for h in f["pieces"])):
            found += [(f, h, strength(h, parts), False) for h in f["pieces"] if mentions(h, parts)]
    return found


def strength(hunk, names):
    return 1 if not names else 4 if touched(hunk) & set(names) else 3 if set(hunk["outer"]) & set(names) else 2


def changed(hunk):
    return sum(1 for l in hunk["lines"] if l[:1] in "+-")


REF = re.compile(r"(?:([\w./-]+\.\w+))?:(\d+)(?:[–-](\d+))?")


def cites(f, h, cited):
    mine = [(lo, hi) for file, lo, hi in cited if not file or ("/" + f["path"]).endswith("/" + file)]
    if not cited:
        return 2
    if any(lo <= h["span"][1] and hi >= h["span"][0] for lo, hi in mine):
        return 0
    if mine and (f["spans"] is None or any(lo <= last and hi >= first for lo, hi in mine for n in touched(h)
                                           for first, last in f["spans"].get(n, []))):
        return 1
    return 3


def place(path):
    raw, files = load_diff(path)
    tokens = MARKDOWN.parse(path.read_text(encoding="utf-8"))
    claims, sections, current, number = {}, {}, [], None
    for i, token in enumerate(tokens):
        if token.type == "inline":
            number = m.group(1) if (m := re.match(r"\s*\*\*(\d+a?)\.", token.content)) else number
            children, words = token.children or [], 0
            for k, child in enumerate(children):
                if child.type == "code_inline":
                    after = re.match(r"\s*(\([^)]*\))?", children[k + 1].content if k + 1 < len(children) else "")
                    cited = [(m.group(1), int(m.group(2)), int(m.group(3) or m.group(2)))
                             for m in REF.finditer(child.content + " " + after.group(0))]
                    lead = words <= 3 or bool(re.match(r"\([\w./-]*:\d", after.group(1) or ""))
                    current += [(f, h, s, by_file, lead and s >= 3, cites(f, h, cited))
                                for f, h, s, by_file in hunks_for(child.content, files)]
                    words += len(child.content.split())
                else:
                    tail = re.split(r"[.!?:](?:\s|$)", child.content)
                    words = (words if len(tail) == 1 else 0) + len(tail[-1].split())
        elif token.type == "paragraph_close" and current:
            claims[i], sections[i], current = current, number, []
    best = {}
    for i, found in claims.items():
        breadth, named = len({id(h) for _, h, *_ in found}), {f["path"] for f, _, _, by_file, *_ in found if by_file}
        for f, h, s, by_file, lead, cited in found:
            key = (s < 3, sections[i] != ("8" if is_test(f["path"]) else "4"), cited, s == 3, not lead,
                   0 if by_file else 1 if f["path"] in named else 2, -s, breadth, i)
            if id(h) not in best or key < best[id(h)][0]:
                best[id(h)] = (key, i, f)
    out = []
    for i, token in enumerate(tokens):
        out.append(token)
        by_file = {}
        for f, h in {id(h): (f, h) for f, h, *_ in claims.get(i, []) if best[id(h)][1] == i}.values():
            by_file.setdefault(f["path"], []).append(h)
        if not by_file:
            continue
        blocks = "".join(diff_html(p, sorted(hs, key=lambda h: h["span"]))
                         for p, hs in sorted(by_file.items(), key=lambda i: is_test(i[0])))
        block = Token("html_block", "", 0)
        block.content = (f"<details class=dx><summary>diff: {sum(changed(h) for hs in by_file.values() for h in hs)}"
                         f" lines</summary>{blocks}</details>")
        out.append(block)
    return out, files, set(best), raw.split("\n", 1)[0].removeprefix("# head ")[:8]


def unplaced(files, placed):
    return [(f, h) for f in files for h in f["pieces"] if id(h) not in placed and changed(h)]


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
    lines += [f"  not placed: {f['path']} {h['header']} ({changed(h)} lines)" for f, h in missing]
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


def version(path):
    """What an approval is pinned to: the head a walkthrough's diff was stored at, or a brief's text (less the
    posting stamp and the **Status:** line, which change when it is approved)."""
    if path.parent.name == "briefs":
        body = re.sub(r"\A\*Posted [^*\n]*\*\s*", "", path.read_text(encoding="utf-8"))
        body = re.sub(r"(?m)^\*\*Status:.*\n?", "", body)
        return hashlib.sha256(body.encode()).hexdigest()[:12]
    diff = path.with_suffix(".diff")
    return diff.read_text(encoding="utf-8").split()[2] if diff.is_file() else None


def item(path):
    return ("brief", path.stem) if path.parent.name == "briefs" else ("pr", str(pr_number(path)))


def decisions(base):
    """{(kind, name): latest decision}, from the project's append-only approvals.jsonl."""
    log = base / "approvals.jsonl"
    found = {}
    for line in log.read_text(encoding="utf-8").splitlines() if log.is_file() else []:
        d = json.loads(line)
        found[(d["kind"], d["name"])] = d
    return found


def standing(path):
    """(latest decision or None, current version, whether an approval stands for the current version)."""
    d, now = decisions(path.parent.parent).get(item(path)), version(path)
    return d, now, bool(d and now and d["action"] == "approve" and d["version"] == now)


def approval_html(path):
    kind, name = item(path)
    d, now, approved = standing(path)
    if now is None:
        return "<p class=when>No diff stored for this walkthrough, so it cannot be approved yet.</p>"
    label = f"#{name}" if kind == "pr" else name
    if approved:
        state = f'<span class=ok>Approved {html.escape(d["at"])} at {now[:8]}.</span>'
        action, text = "withdraw", "Withdraw approval"
    else:
        state = (f'<span class=bad>You approved an earlier version ({html.escape(d["version"][:8])}); this is '
                 f'{now[:8]}.</span>' if d and d["action"] == "approve" else "<span>Not approved.</span>")
        action, text = "approve", f"Approve {label} at {now[:8]}"
    fields = (("kind", kind), ("name", name), ("version", now), ("action", action))
    return (f'<form class=ap method=post action="/{quote(path.parent.parent.name)}/approve">{state} '
            + "".join(f'<input type=hidden name={k} value="{html.escape(v)}">' for k, v in fields)
            + f"<button>{text}</button></form>")


def badge(path):
    d, _, approved = standing(path)
    if approved:
        return " <b class=ok>approved</b>"
    if d and d["action"] == "approve":
        return " <b class=stale>approved an older version</b>"
    return " <b class=no>not approved</b>"


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
        mark = " · approved" if standing(path)[2] else ""
        return (f'{row.group(0)}<tr class=wt><td colspan={width}><details><summary>Walkthrough #{found.group(1)}{mark}'
                f'</summary>{approval_html(path)}{render_walkthrough(path)}{approval_html(path)}<button class=fold onclick="var d=this.closest(\'details\');'
                f'd.open=false;d.scrollIntoView({{block:\'nearest\'}})">Collapse walkthrough #{found.group(1)}'
                f'</button></details></td></tr>')
    body = re.sub(r"<tr>\s*<td.*?</tr>", expand, matrix_html, flags=re.S)
    note = ("<p class=when>Could not ask GitHub which PRs are open; showing every row.</p>" if still_open is None
            else f"<p class=when>Hidden, merged or closed: {', '.join(gone)}</p>" if gone else "")
    return note + body


STYLE = """<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:1400px;margin:24px 32px;padding:0;color:#222}
a{color:#0b5cad}table{border-collapse:collapse;margin:12px 0}td,th{border:1px solid #ccc;padding:4px 8px;vertical-align:top}
th{background:#f3f3f3;text-align:left}pre{background:#f6f6f6;padding:10px;overflow-x:auto}code{background:#f3f3f3;padding:0 3px;overflow-wrap:anywhere}
tr.wt>td{max-width:0}tr.wt table{display:block;max-width:100%;overflow-x:auto}
nav{margin-bottom:16px}nav a{margin-right:16px;font-weight:600}.when{color:#777;font-size:13px;margin-left:8px}
tr.wt>td{border-top:none;background:#fcfcfc}tr.wt summary{cursor:pointer;color:#0b5cad;font-size:13px}
button.fold{margin:12px 0 4px;padding:4px 12px;cursor:pointer}
form.ap{margin:8px 0 14px;padding:8px 10px;border:1px solid #ccc;background:#fafafa}
form.ap button{margin-left:10px;padding:4px 12px;cursor:pointer}
#foldfix{position:fixed;right:12px;top:50%;display:none;padding:6px 10px;cursor:pointer;box-shadow:0 1px 4px #0003}
details.dx{margin:4px 0 10px}details.dx>summary{cursor:pointer;color:#0b5cad;font-size:13px}
.diff{border:1px solid #ddd;margin:6px 0}.df{background:#eef;padding:2px 8px;font:13px monospace}
.diff pre{margin:0;padding:4px 0;font-size:12.5px;background:#fff}
.diff pre span{display:block;padding:0 8px;white-space:pre-wrap;overflow-wrap:anywhere}
.da{background:#e6ffec}.dd{background:#ffebe9}.dh{color:#777;background:#f6f8fa}
.dm{background:#fffbe6}.dm del{background:#ffc1bd;color:#86181d}.dm ins{background:#abf2bc;text-decoration:none}
i[class^=y]{font-style:normal}[class^=yk],.yow{color:#0000ff}[class^=ys]{color:#a31515}
[class^=yc]{color:#008000;font-style:italic}.ynf,.ync,.ynd{color:#795e26}.ynb,.ybp{color:#267f99}[class^=ym]{color:#098658}
.ok{color:#1a7f37}.bad{color:#b42318;font-weight:600}
li b{font-size:12px;margin-left:8px}li b.stale{color:#9a6700}li b.no{color:#777;font-weight:400}
::highlight(same){background:#ffe58a}
</style>"""

SCRIPT = """<button id=foldfix></button><script>
// Open sections are remembered per page: each <details> is keyed by its summaries' text, outermost first,
// plus how many earlier sections share that text.
const store = "open:" + location.pathname;
const keyed = new Map(), seen = {};
for (const d of document.querySelectorAll("details")) {
  const names = [];
  for (let e = d; e; e = e.parentElement.closest("details")) names.unshift(e.querySelector(":scope>summary")?.textContent);
  const name = names.join(" / ");
  seen[name] = (seen[name] || 0) + 1;
  keyed.set(d, `${name} #${seen[name]}`);
}
const remembered = new Set(JSON.parse(localStorage.getItem(store) || "[]"));
for (const [d, key] of keyed) if (remembered.has(key)) d.open = true;
document.addEventListener("toggle", () => {
  localStorage.setItem(store, JSON.stringify([...keyed].filter(([d]) => d.open).map(([, key]) => key)));
  placeFold();
}, true);

// A collapse button pinned to the right edge while the middle of the screen is inside an open walkthrough.
const fold = document.getElementById("foldfix");
let showing = null;
function placeFold() {
  showing = [...document.querySelectorAll("tr.wt details[open]")].find(d => {
    const r = d.getBoundingClientRect();
    return r.top < innerHeight / 2 && r.bottom > innerHeight / 2;
  }) || null;
  fold.style.display = showing ? "block" : "none";
  if (showing) fold.textContent = "Collapse " + showing.querySelector(":scope>summary").textContent;
}
fold.onclick = () => {
  const row = showing.closest("tr").previousElementSibling;
  showing.open = false;
  row.scrollIntoView({block: "center"});
};
addEventListener("scroll", placeFold, {passive: true});
addEventListener("resize", placeFold);
placeFold();

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
                                  f'<a href="/{project}/reviews/">Reviews</a><a href="/{project}/briefs/">Briefs</a>'
                                  f'<a href="/{project}/reports/">Reports</a></nav>')
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>{STYLE}<nav>{nav}</nav>{sub}{body}"
            f"{SCRIPT}")


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
        if len(parts) == 2 and parts[1] in ("briefs", "reports"):
            files = sorted(folder.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True) if folder.is_dir() else []
            body = f"<h1>{parts[1].title()}</h1><ul>" + "".join(
                f'<li><a href="/{project}/{parts[1]}/{f.name}">{html.escape(f.stem)}</a>{badge(f) if parts[1] == "briefs" else ""}'
                f'<span class=when>{time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime))}</span></li>'
                for f in files) + "</ul>"
            return self.send(page(project, parts[1], body))
        if len(parts) == 2:
            files = sorted(folder.glob("pr*.md"), key=pr_number, reverse=True) if folder.is_dir() else []
            still_open = open_prs(base)

            def items(chosen):
                return "".join(
                    f'<li><a href="/{project}/{parts[1]}/{f.name}">#{pr_number(f)}</a>{badge(f) if parts[1] == "walkthroughs" else ""}'
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
        if parts[1] in ("walkthroughs", "briefs"):
            body = approval_html(path) + body
        self.send(page(project, path.stem, body))

    def do_POST(self):
        """An approve or withdraw click. Refused unless it comes from this dashboard's own page (same origin) and
        names the version that page showed, so a stale tab cannot approve a newer head or brief."""
        parts = [unquote(p) for p in self.path.split("?")[0].split("/") if p]
        host = f"http://{self.headers.get('Host', '')}"
        if len(parts) != 2 or parts[1] != "approve" or parts[0] in (".", "..") or "/" in parts[0] \
                or self.headers.get("Origin") != host or not (ROOT / parts[0]).is_dir():
            return self.send(page(None, "Refused", "<p>Refused.</p>"), 403)
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
        kind, name, seen, action = (form.get(k, [""])[0] for k in ("kind", "name", "version", "action"))
        valid = (kind == "pr" and name.isdigit()) or (kind == "brief" and BRIEF_NAME.fullmatch(name))
        path = valid and target(parts[0], "walkthrough" if kind == "pr" else "brief", name)
        if not path or action not in ("approve", "withdraw") or not path.is_file() or version(path) != seen:
            return self.send(page(parts[0], "Out of date", "<p>This page is out of date. Reload it and look again."
                                                           "</p>"), 409)
        record(parts[0], kind, name, seen, action, "dashboard")
        back = self.headers.get("Referer", "")
        self.send_response(303)
        self.send_header("Location", back[len(host):] if back.startswith(host + "/") else f"/{quote(parts[0])}/")
        self.send_header("Content-Length", "0")
        self.end_headers()

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


def record(project, kind, name, seen, action, via):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with (ROOT / project / "approvals.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"kind": kind, "name": name, "version": seen, "action": action, "at": stamp,
                              "via": via}) + "\n")


def record_chat(args):
    """An approval the owner gave in chat, pinned to what is posted now so the page shows it."""
    path = target(args.project, "walkthrough" if args.kind == "pr" else "brief", args.name)
    now = version(path) if path.is_file() else None
    if now is None:
        sys.exit(f"owner-dash: nothing approvable posted at {path}")
    record(args.project, args.kind, args.name, now, args.action, "chat")
    print(f"{args.kind} {args.name} {args.action} at {now} (chat)")


def list_approvals(args):
    base = ROOT / args.project
    for (kind, name), d in sorted(decisions(base).items()):
        path = target(args.project, "walkthrough" if kind == "pr" else "brief", name)
        now = version(path) if path.is_file() else None
        state = "current" if now == d["version"] else f"STALE: posted is now {now}"
        print(f"{kind} {name} {d['action']} {d['version']} {d['at']} ({state})")


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
    sub.add_parser("approvals", help="each PR's and brief's latest decision, and whether it matches what is posted")
    p_rec = sub.add_parser("record", help="record an approval or withdrawal the owner gave in chat")
    p_rec.add_argument("action", choices=("approve", "withdraw"))
    p_rec.add_argument("kind", choices=("pr", "brief"))
    p_rec.add_argument("name")
    args = parser.parse_args()
    args.project = args.project or default_project()
    if args.command == "serve":
        return serve(args)
    if args.command == "approvals":
        return list_approvals(args)
    if args.command == "record":
        return record_chat(args)
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
        key = {"matrix": "", "brief": "<name> "}.get(args.kind, "<N> ")
        parser.error(f"{args.command} {args.kind} takes {key}{'<file|->' if args.command == 'put' else ''}".strip())
    args.number = None if args.kind == "matrix" else args.rest[0]
    args.file = args.rest[-1] if args.command == "put" else None
    (put if args.command == "put" else show)(args)


if __name__ == "__main__":
    main()
