import importlib
import shutil
import sys
from pathlib import Path

SK = Path(__file__).resolve().parent.parent
M = SK / "tests" / "_mutants"
MUTANTS = [
    ("edited = present on both sides",
     'return {"edited": sorted(n for n in set(base) & set(head) if base[n] != head[n]),',
     'return {"edited": sorted(n for n in set(base) & set(head)),',
     "test_function_changes_compares_each_function_source"),
    ("recorded review always current",
     'latest = dict(latest, current=bool(head) and pr["head_sha"].startswith(head))',
     'latest = dict(latest, current=True)',
     "test_recorded_review_is_current_only_at_its_head"),
    ("cwd matched by name substring",
     "if cwd and Path(cwd).is_relative_to(roots[r]):",
     "if cwd and names[r] in cwd:",
     "test_worktree_sessions_read_structured_fields_only"),
    ("missing commit reads as unsigned",
     "return {sha[:7]: found.get(sha[:7]) for sha in shas}",
     "return {sha[:7]: found.get(sha[:7], []) for sha in shas}",
     "test_commit_signers_come_from_git_trailers"),
    ("pr-link from another repo counted",
     'if rec.get("prRepository") == repo and rec.get("prNumber") in wanted:',
     'if rec.get("prNumber") in wanted:',
     "test_pr_touches_count_links_and_urls_of_this_repo_only"),
    ("review recorded without verdict",
     'if args.kind == "review" and not args.verdict:',
     "if False:",
     "test_review_record_needs_a_verdict"),
]


def main():
    source = (SK / "scripts" / "tracker.py").read_text(encoding="utf-8")
    M.mkdir(exist_ok=True)
    survived = 0
    try:
        for label, old, new, test in MUTANTS:
            assert source.count(old) == 1, f"mutation site not unique: {old}"
            (M / "tracker.py").write_text(source.replace(old, new), encoding="utf-8")
            for mod in ("tracker", "test_tracker"):
                sys.modules.pop(mod, None)
            sys.path[:] = [str(M)] + [p for p in sys.path if Path(p) != SK / "scripts"]
            tracker = importlib.import_module("tracker")
            assert Path(tracker.__file__).parent == M, tracker.__file__
            sys.path.insert(1, str(SK / "tests"))
            T = importlib.import_module("test_tracker")
            sys.path[:] = [p for p in sys.path if Path(p) != SK / "scripts"]
            try:
                getattr(T, test)()
                survived += 1
                print(f"SURVIVED  {label}")
            except AssertionError as exc:
                print(f"killed    {label} -> {str(exc).splitlines()[0][:100] if str(exc) else 'assert'}")
    finally:
        shutil.rmtree(M, ignore_errors=True)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
