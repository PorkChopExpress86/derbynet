"""A `ui` page that changes a photo while it shows it takes the photo's URL
from `photos.page_url()` or `photos.page_thumbnail_url()`, whose cache
breaker is the file's time in nanoseconds (#47 retro). `Repository.url()`
and `photos.thumbnail_url()` keep PHP's breaker, which counts seconds: a turn
in the same second as the last change kept the browser's copy of the work
render, and #42's crop box landed on the stale frame. A `ui` module that
asks for the `work` render, or a URL of a repository, any other way fails
here."""

import ast
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1]
SOURCES = sorted(p for p in UI.rglob("*.py") if "tests" not in p.parts)
PAGE_URLS = ("page_url", "page_thumbnail_url")
SECONDS_URLS = ("thumbnail_url",)


def _callee(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def offenders(source: str) -> list[str]:
    tree = ast.parse(source)
    through_page_url = {
        id(n)
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and _callee(call) in PAGE_URLS
        for n in ast.walk(call)
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (
            _callee(node) in SECONDS_URLS
            or (
                _callee(node) == "url"
                and isinstance(node.func, ast.Attribute)
                and "repo" in ast.unparse(node.func.value)
            )
        ):
            found.append(f"{node.lineno}: {ast.unparse(node.func)}()")
        work = (isinstance(node, ast.Attribute) and node.attr == "WORK") or (
            isinstance(node, ast.Constant) and node.value == "work"
        )
        if work and id(node) not in through_page_url:
            found.append(f"{node.lineno}: the work render outside page_url()")
    return found


@pytest.mark.parametrize("path", SOURCES, ids=[str(p.relative_to(UI)) for p in SOURCES])
def test_ui_photo_urls_carry_the_nanosecond_breaker(path):
    assert offenders(path.read_text()) == []


@pytest.mark.parametrize(
    "source",
    [
        "url = repository.url(name, photos.WORK)",
        "url = photos.thumbnail_url(repository, name)",
        "url = photo_url(repository, folder, path, 'work')",
    ],
    ids=["repository-url", "thumbnail-url", "work-render"],
)
def test_a_seconds_breaker_is_flagged(source):
    assert offenders(source)


def test_page_url_passes():
    assert offenders("url = photos.page_url(repository, name, photos.WORK)") == []
