"""plan-checks.yml runs, on a PR that changes only plan/, each source check
that reads plan/ (D128). server.yml skips such a PR, and #346's push ran
none of them; removing one decision row fails test_deviations.py (#346
retro). A source check reads plan/ when it names the folder as a string
literal, lists every tracked file with `git ls-files`, or uses a
tests/file_kinds.py function that lists them, from any module (#431): a new
one fails here until the workflow runs it. This file is a source check too,
and spells neither literal whole.
"""

import ast
import re

from tests.workflow_steps import ROOT, WORKFLOWS, steps

SERVER = ROOT / "server"
TEST_FILE = re.compile(r"[\w/]*tests/test_\w+\.py")
READS_PLAN = re.compile(r'"(?:plan|ls-files)"')


def source_checks() -> set[str]:
    text = (SERVER / "tools" / "source-checks.sh").read_text()
    return set(TEST_FILE.findall(text))


def plan_checks() -> set[str]:
    text = (WORKFLOWS / "plan-checks.yml").read_text()
    found = [s for s in steps(text) if "pytest" in s.script]
    assert len(found) == 1, found
    assert found[0].runs, "the step has if: false"
    return set(TEST_FILE.findall(found[0].script))


def tracked_listers(text: str) -> set[str]:
    """The functions of file_kinds' `text` that list every tracked file:
    tracked(), and each function that calls one of them, however deep."""
    calls = {
        node.name: {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        for node in ast.parse(text).body
        if isinstance(node, ast.FunctionDef)
    }
    found = {"tracked"}
    while grown := {name for name, called in calls.items() if called & found} - found:
        found |= grown
    return found


def reads_plan(text: str, listers: set[str]) -> bool:
    """A check's `text` names a literal, or uses one of `listers`: imported
    by name from any module, or as an attribute of tests.file_kinds imported
    as a module."""
    tree = ast.parse(text)
    names, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if node.module == "tests" and alias.name == "file_kinds"
            )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "tests.file_kinds"
            )
    attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and ast.unparse(node.value) in modules
    }
    return READS_PLAN.search(text) is not None or bool((names | attributes) & listers)


def test_plan_checks_runs_each_source_check_that_reads_plan():
    listers = tracked_listers((SERVER / "tests" / "file_kinds.py").read_text())
    readers = {
        name
        for name in source_checks()
        if reads_plan((SERVER / name).read_text(), listers)
    }
    assert readers == plan_checks()


def test_the_list_is_the_checks_found_on_2026_11_16_and_by_431():
    """A rule that found none would pass above with an empty step. #431
    found test_subshell_state.py and test_arithmetic_operands.py, which lint
    plan/'s scripts through shell_files() and name no literal. The #470
    retro added test_lead_rulings.py and test_docstring_position.py, which
    read plan/ through `git ls-files`."""
    assert plan_checks() == {
        "legacy_api/tests/test_registry.py",
        "tests/test_deviations.py",
        "tests/test_docker_checks.py",
        "tests/test_listing_order.py",
        "tests/test_pipefail_pipes.py",
        "tests/test_subshell_state.py",
        "tests/test_arithmetic_operands.py",
        "tests/test_lead_rulings.py",
        "tests/test_docstring_position.py",
    }


def test_a_function_that_calls_a_lister_lists_too():
    kinds = (
        "def tracked():\n    return []\n"
        "def scripts():\n    return [n for n in tracked()]\n"
        "def shell():\n    return scripts()\n"
        "def deeper():\n    return shell()\n"
        "def faces():\n    return []\n"
    )
    assert tracked_listers(kinds) == {"tracked", "scripts", "shell", "deeper"}


def test_a_lister_used_through_its_module_reads_plan():
    """#431 review: `from tests import file_kinds` and then
    `file_kinds.shell_files()` lists plan/'s scripts as the import by name
    does; a function that lists no tracked file reads no plan/."""
    listers = {"tracked", "shell_files"}
    for text, expected in (
        ("from tests import file_kinds\n\nfile_kinds.shell_files()\n", True),
        ("from tests import file_kinds as kinds\n\nkinds.shell_files()\n", True),
        ("import tests.file_kinds\n\ntests.file_kinds.shell_files()\n", True),
        ("import tests.file_kinds as kinds\n\nkinds.tracked()\n", True),
        ("from tests.file_kinds import shell_files\n", True),
        ("from tests import file_kinds\n\nfile_kinds.headshots()\n", False),
        ("from tests import other\n\nother.shell_files()\n", False),
        ("from other import file_kinds\n\nfile_kinds.shell_files()\n", False),
        ("import other as kinds\n\nkinds.shell_files()\n", False),
    ):
        assert reads_plan(text, listers) is expected, text
