import ast
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]


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
