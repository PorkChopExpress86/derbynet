"""Every management command has a test that runs it.

The #40 review found `manage.py build_css` merged with no test, and its first
tests then found a real defect. A command a test never names goes untested.
"""

import re
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
APPS = [p.parent.parent for p in SERVER.glob("*/management/commands")]


def _commands():
    for app in APPS:
        for path in sorted((app / "management" / "commands").glob("*.py")):
            if path.stem != "__init__":
                yield path


def _test_sources():
    return [
        path.read_text()
        for app in [*APPS, SERVER / "tests"]
        for path in app.rglob("test_*.py")
        if path != Path(__file__).resolve()
    ]


def test_the_scan_finds_the_commands():
    assert {p.stem for p in _commands()} >= {"build_css", "create_test_event"}


def test_every_command_is_named_by_a_test():
    sources = _test_sources()
    untested = []
    for path in _commands():
        name = re.escape(path.stem)
        named = re.compile(rf"[\"']{name}[\"']|commands(\.|\s+import\s+){name}\b")
        if not any(named.search(source) for source in sources):
            untested.append(str(path.relative_to(SERVER)))
    assert not untested, (
        "These commands have no test that runs them (call_command(name) "
        f"or an import of the command module): {untested}"
    )
