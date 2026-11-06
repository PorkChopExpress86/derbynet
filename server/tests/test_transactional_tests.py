"""A test that runs outside a transaction restores the seeded rows (#212 retro).

pytest-django ends a `django_db(transaction=True)` test by flushing every
table, the content types and the rows the migrations seeded (roles, award
types) included. Without `serialized_rollback=True` the next such test on
the same xdist worker meets an empty database: in #212, `test_export.py`
ran before `test_backups.py` on one worker, and the backups tests' restore
failed on duplicate content types. This lint fails each `django_db` mark
with `transaction=True` and no `serialized_rollback=True`, and each test or
fixture outside the browser harness that asks for `transactional_db` or
`live_server`. The browser tests (`ui/tests/browser/`) run apart, with `-m
browser`, and their `_database` fixture seeds the rows again after each
flush.
"""

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
BROWSER = SERVER / "ui" / "tests" / "browser"
TRANSACTIONAL_FIXTURES = {"transactional_db", "live_server"}


def _is_true(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_django_db(func) -> bool:
    return (isinstance(func, ast.Attribute) and func.attr == "django_db") or (
        isinstance(func, ast.Name) and func.id == "django_db"
    )


def unrestored(source: str) -> list[str]:
    """`line: what` for each test in `source` that flushes the database and
    leaves the seeded rows gone."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _is_django_db(node.func):
            keywords = {k.arg: k.value for k in node.keywords}
            if _is_true(keywords.get("transaction")) and not _is_true(
                keywords.get("serialized_rollback")
            ):
                found.append(
                    f"{node.lineno}: django_db(transaction=True) "
                    "without serialized_rollback=True"
                )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for arg in node.args.args:
                if arg.arg in TRANSACTIONAL_FIXTURES:
                    found.append(f"{node.lineno}: {node.name} asks for {arg.arg}")
    return found


def test_every_transactional_test_restores_the_seeded_rows():
    found = []
    paths = [SERVER / "conftest.py"]
    for top in sorted(SERVER.iterdir()):
        if top.is_dir() and not top.name.startswith(".") and top.name != "var":
            paths += sorted(top.rglob("test_*.py")) + sorted(top.rglob("conftest.py"))
    for path in paths:
        relative = path.relative_to(SERVER)
        if BROWSER in path.parents:
            continue
        found += [f"{relative}:{line}" for line in unrestored(path.read_text())]
    assert found == [], "add serialized_rollback=True to each"


def test_the_lint_flags_each_way_a_test_can_flush_the_seeded_rows():
    assert unrestored(
        "@pytest.mark.django_db(transaction=True)\ndef test_a(): pass\n"
    ) == ["1: django_db(transaction=True) without serialized_rollback=True"]
    assert unrestored(
        "pytestmark = pytest.mark.django_db(transaction=True, "
        "serialized_rollback=False)\n"
    ) == ["1: django_db(transaction=True) without serialized_rollback=True"]
    assert unrestored("def test_b(transactional_db): pass\n") == [
        "1: test_b asks for transactional_db"
    ]
    assert unrestored("@pytest.fixture\ndef server(live_server): pass\n") == [
        "2: server asks for live_server"
    ]


def test_the_lint_passes_a_test_that_restores_them():
    assert (
        unrestored(
            "concurrent = pytest.mark.django_db(transaction=True, "
            "serialized_rollback=True)\n"
            "@pytest.mark.django_db\ndef test_c(db): pass\n"
        )
        == []
    )
