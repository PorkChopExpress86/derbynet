"""Every path of a photo repository comes from `Repository.file()`, which
refuses a name that would leave the folder (#47 retro). #47's `page_url()`
joined the directory with a racer's column itself and read the file before
its review added a check. A join of `.directory` with `/` anywhere else in
`media` fails here."""

import ast
from pathlib import Path

import pytest

from media.photos import CROPPED_FOLDER, Repository

MEDIA = Path(__file__).resolve().parents[1]
SOURCES = sorted(p for p in MEDIA.rglob("*.py") if "tests" not in p.parts)


def joins(source: str) -> list[int]:
    """Lines outside `Repository.file()` that put `<x>.directory` on the
    left of a `/`."""
    tree = ast.parse(source)
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Repository":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "file":
                    allowed |= {id(n) for n in ast.walk(item)}
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Div)
        and any(
            isinstance(n, ast.Attribute) and n.attr == "directory"
            for n in ast.walk(node.left)
        )
        and id(node) not in allowed
    ]


@pytest.mark.parametrize("path", SOURCES, ids=[p.name for p in SOURCES])
def test_only_repository_file_joins_the_directory(path):
    assert joins(path.read_text()) == []


def test_a_direct_join_is_flagged():
    assert joins("def page_url(repo, path):\n    return repo.directory / path\n") == [2]


@pytest.mark.parametrize(
    ("name", "folder"),
    [
        ("../x.jpg", None),
        ("a/b.jpg", None),
        ("..", None),
        ("x.jpg", "../up"),
        ("", None),
    ],
)
def test_file_refuses_a_name_that_leaves_the_folder(tmp_path, name, folder):
    repo = Repository("head", tmp_path, "headshot")
    with pytest.raises(ValueError):
        repo.file(name, folder)


def test_file_joins_a_name_and_a_folder(tmp_path):
    repo = Repository("head", tmp_path, "headshot")
    assert repo.file("x.jpg", CROPPED_FOLDER) == tmp_path / CROPPED_FOLDER / "x.jpg"
