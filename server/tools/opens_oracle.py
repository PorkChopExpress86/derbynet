"""The runtime oracle for plan/bin/affected-tests' testing/ rules (#470): a
pytest plugin that records each read of a tracked testing/ file and the test
that made it, and a report that joins those reads with the tool's mapping.

#431 described its oracle in PR #462 but never committed it; this is that
oracle, rebuilt and kept.

Usage, from server/, in a clean worktree, on the load lock for a long run:

    ORACLE_DIR=DIR PYTHONPATH=tools uv run pytest -p opens_oracle -n auto -m browser
    python3 -I tools/opens_oracle.py DIR

The plugin installs `sys.addaudithook` in each pytest process, and each
process writes `DIR/<pid>.jsonl`. It records `open` of a tracked path under
<repo>/testing/, `os.scandir` of a folder there, and
`subprocess.Popen` arguments that name a path there. Playwright's driver,
a child process, reads the files a test hands `set_input_files`, so the
plugin wraps Playwright's `convert_input_files` and records those paths as
`upload`. Each record names the test running, or, at import time, the
server modules whose top level is on the stack. The test running is one
value for the process, so a read in another thread, such as the live
server's, counts for it.

Blind spots: a child process's own reads are not traced (no strace); only
the testing/ paths on its command line are recorded, and a relative one is
read against the pytest process's cwd, not the child's `cwd=`. A C
library's open is not seen.
"""

import ast
import collections
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

STATE = {"test": None, "fd": None, "repo": None, "tracked": frozenset()}
EVENTS = {"open", "os.scandir", "subprocess.Popen"}


def _write(row):
    os.write(STATE["fd"], (json.dumps(row) + "\n").encode())


def _record(kind, path):
    modules, frame = [], sys._getframe(2)
    while frame is not None:
        if frame.f_code.co_name == "<module>":
            name = frame.f_code.co_filename
            modules.append(os.path.relpath(name, STATE["repo"] + "/server"))
        frame = frame.f_back
    _write({"kind": kind, "path": path, "test": STATE["test"], "modules": modules})


def _rel(path):
    """`path` relative to the repository when it is under testing/."""
    if not isinstance(path, (str, bytes, os.PathLike)):
        return None
    full = os.path.normpath(os.path.join(os.getcwd(), os.fsdecode(path)))
    if full.startswith(STATE["repo"] + "/testing/"):
        return full[len(STATE["repo"]) + 1 :]
    return None


def _hook(event, args):
    if event not in EVENTS:
        return
    if event == "open":
        rel = _rel(args[0])
        if rel in STATE["tracked"]:
            _record("open", rel)
    elif event == "os.scandir":
        rel = _rel(args[0])
        if rel:
            _record("list", rel)
    else:
        for arg in args[1]:  # POSIX's Popen hands the audit hook a list
            for word in os.fsdecode(arg).split():
                rel = _rel(word)
                if rel:
                    _record("popen", rel)


def _record_uploads():
    """Wrap convert_input_files where Playwright's frames and element
    handles call it, to record each testing/ path a test uploads."""
    try:
        from playwright._impl import _element_handle, _frame
    except ImportError:
        return
    convert = _frame.convert_input_files

    async def recorded(files, context):
        for item in files if isinstance(files, (list, tuple)) else [files]:
            rel = _rel(item)
            if rel in STATE["tracked"]:
                _record("upload", rel)
        return await convert(files, context)

    _frame.convert_input_files = recorded
    _element_handle.convert_input_files = recorded


def pytest_configure(config):
    repo = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=config.rootpath,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "testing/"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    STATE["repo"] = os.path.realpath(repo)
    STATE["tracked"] = frozenset(tracked.split("\0"))
    out = os.environ["ORACLE_DIR"]
    os.makedirs(out, exist_ok=True)
    STATE["fd"] = os.open(
        os.path.join(out, f"{os.getpid()}.jsonl"),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o644,
    )
    sys.addaudithook(_hook)
    _record_uploads()


def pytest_runtest_protocol(item, nextitem):
    STATE["test"] = item.nodeid


def pytest_runtest_logfinish(nodeid, location):
    STATE["test"] = None


def load_tool(repo):
    path = repo / "plan" / "bin" / "affected-tests"
    loader = importlib.machinery.SourceFileLoader("affected_tests", str(path))
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(loader.name, loader)
    )
    loader.exec_module(module)
    return module


def imported_files(server, rel):
    """The server/ .py files `rel` imports, at any depth and inside
    functions too: the oracle's own graph, not affected-tests'. A relative
    import is not followed."""
    seen, todo = set(), [rel]
    while todo:
        for node in ast.walk(ast.parse((server / todo.pop()).read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            else:
                continue
            for name in names:
                for found in (
                    f"{name.replace('.', '/')}.py",
                    f"{name.replace('.', '/')}/__init__.py",
                ):
                    if found not in seen and (server / found).is_file():
                        seen.add(found)
                        todo.append(found)
    return seen


def read_rows(out):
    """The records every process wrote under `out`."""
    return [json.loads(line) for f in sorted(out.glob("*.jsonl")) for line in f.open()]


def pairs(rows, server, test_files):
    """(testing/ path, test file) -> how it was read: `open`, `list`,
    `popen` or `upload` while the test ran, or `import-time` when a module
    the test file imports read it at its top level."""
    found = collections.defaultdict(set)
    imports = {test: imported_files(server, test) for test in test_files}
    for row in rows:
        if row["test"] is not None:
            found[row["path"], row["test"].split("::")[0]].add(row["kind"])
            continue
        for module in row["modules"]:
            for test in test_files:
                if module in imports[test]:
                    found[row["path"], test].add("import-time")
    return found


def mapped(tool, tree, path, test):
    """Whether affected-tests' cover_testing names `test` among the targets
    of testing/`path`. A folder or "everything" counts as no mapping."""
    plan = tool.Plan(root=tree.root, from_git=False, base="origin/master")
    tool.cover_testing(plan, tree, path)
    return test in {t for _r, _n, ts in plan.covers[path] for t in ts}


def main(argv):
    out = Path(argv[1])
    repo = Path(__file__).resolve().parents[2]
    server = repo / "server"
    rows = read_rows(out)
    tool = load_tool(repo)
    tree = tool.Tree(repo)
    tests = [t for t in tree.test_files() if tool.is_browser_test(tree, t)]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    print(f"worktree {repo} head {head}; uncommitted: {'yes' if dirty else 'no'}")
    found = pairs(rows, server, tests)
    print(f"pairs {len(found)}; testing/ paths {len({path for path, _ in found})}")
    unmapped = 0
    for (path, test), kinds in sorted(found.items()):
        if kinds == {"list"}:
            how = "listing"  # a folder: its files are read by name
        elif mapped(tool, tree, path, test):
            how = "mapped"
        else:
            how, unmapped = "UNMAPPED", unmapped + 1
        print(f"{how}\t{','.join(sorted(kinds))}\t{path}\t{test}")
    print(f"unmapped file pairs {unmapped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
