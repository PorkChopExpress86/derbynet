import ast
import subprocess
import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]
SERVER_DIR = ENGINE_DIR.parents[1]

# Imports every engine module and schedules a round, which runs the imports
# made inside functions, in an interpreter that has not loaded Django.
LOADS_NO_DJANGO = """
import importlib, pkgutil, sys
import racing.engine as engine
names = [m.name for m in pkgutil.walk_packages(engine.__path__, "racing.engine.")]
for name in names:
    importlib.import_module(name)
from racing.engine.scheduling.round import make_chart
make_chart(list(range(10)), 4, 2)
make_chart(list(range(10)), 4, 1, rotation=True, unused_lane_mask=1)
print(len(names))
assert "django" not in sys.modules, sorted(m for m in sys.modules if "django" in m)
"""


def test_engine_modules_load_no_django_when_run():
    result = subprocess.run(
        [sys.executable, "-c", LOADS_NO_DJANGO],
        capture_output=True,
        text=True,
        cwd=SERVER_DIR,
    )
    assert result.returncode == 0, result.stderr
    modules = sum(1 for p in ENGINE_DIR.rglob("*.py") if p.name != "__init__.py")
    assert int(result.stdout) >= modules


def django_imports(source):
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] == "django"]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] == "django":
                found.append(node.module)
    return found


def test_engine_imports_no_django():
    offenders = {}
    for path in sorted(ENGINE_DIR.rglob("*.py")):
        found = django_imports(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path.relative_to(ENGINE_DIR))] = found
    assert offenders == {}


def test_the_walk_sees_every_import_form():
    assert django_imports("import django") == ["django"]
    assert django_imports("import django.db as db") == ["django.db"]
    assert django_imports("from django.db import models") == ["django.db"]
    assert django_imports("def f():\n    from django import conf") == ["django"]
    assert django_imports("import json\nfrom . import x\nfrom os import path") == []
