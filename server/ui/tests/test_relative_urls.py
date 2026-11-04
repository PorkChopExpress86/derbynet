"""The new pages name a legacy `.php` URL from the server root (#50 retro).

The race dashboard opened `window.open('timer.php')`, which from
`/dashboard/` is `/dashboard/timer.php`, a 404 (#41, found by #50). Its test
read `endswith("/timer.php")`, which the wrong URL also passed. PHP's pages
lived at the root, so a copied relative URL worked there and breaks here.

The browser timer's template (`ui/timer/browser.html`) keeps PHP's relative
URLs on purpose: `/timer.php` answers them at the same paths (#50), and
`manage.py build_timer --check` keeps it equal to its source.
"""

import re
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[2]
UI = SERVER / "ui"
GENERATED = {UI / "templates" / "ui" / "timer" / "browser.html"}
SCRIPTS = sorted((UI / "static" / "ui").glob("*.js"))
TEMPLATES = sorted(p for p in (UI / "templates").rglob("*.html") if p not in GENERATED)

# A quoted string that starts with a relative path and names a `.php` file.
JS_RELATIVE = re.compile(r"""(['"`])(?![/{$]|https?:)[\w./-]*\.php\b[^'"`]*\1""")
# An attribute whose value starts with a relative path to a `.php` file.
ATTR_RELATIVE = re.compile(
    r"""\b(?:href|src|action|formaction|hx-(?:get|post|put|patch|delete)"""
    r"""|data-[\w-]*url)=(['"])(?![/{]|https?:)[\w./-]*\.php\b[^'"]*\1"""
)
JS_COMMENT = re.compile(r"^\s*(//|\*|/\*)")
TEMPLATE_COMMENT = re.compile(r"{#.*?#}|<!--.*?-->", re.S)


def js_offenders(source: str, filename: str = "page.js") -> list[str]:
    return [
        f"{filename}:{n}: {found.group()}"
        for n, line in enumerate(source.splitlines(), 1)
        if not JS_COMMENT.match(line)
        for found in JS_RELATIVE.finditer(line)
    ]


def template_offenders(source: str, filename: str = "page.html") -> list[str]:
    source = TEMPLATE_COMMENT.sub(lambda m: "\n" * m.group().count("\n"), source)
    return [
        f"{filename}:{source.count(chr(10), 0, found.start()) + 1}: {found.group()}"
        for found in ATTR_RELATIVE.finditer(source)
    ]


@pytest.mark.parametrize("path", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_scripts_name_php_urls_from_the_root(path):
    assert js_offenders(path.read_text(), path.name) == []


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=[str(p.relative_to(UI)) for p in TEMPLATES]
)
def test_templates_name_php_urls_from_the_root(path):
    assert template_offenders(path.read_text(), str(path.relative_to(UI))) == []


@pytest.mark.parametrize(
    "source",
    [
        "window.open('timer.php', 'timer3');",
        'fetch("action.php", { method: "POST" });',
        "location.href = `../kiosk.php?address=${a}`;",
    ],
    ids=["window-open", "fetch", "template-literal"],
)
def test_a_relative_php_url_in_a_script_is_flagged(source):
    assert len(js_offenders(source)) == 1


@pytest.mark.parametrize(
    "source",
    [
        "window.open('/timer.php', 'timer3');",
        "window.open(url, 'timer3');",
        "// PHP's page posts to the relative `action.php`.",
        "fetch(`${actionUrl}?query=poll`);",
    ],
    ids=["root", "from-the-page", "comment", "variable"],
)
def test_a_root_php_url_in_a_script_passes(source):
    assert js_offenders(source) == []


@pytest.mark.parametrize(
    "source",
    [
        '<a href="timer.php">Timer</a>',
        "<button hx-get='kiosk.php?page=x'>",
        '<div data-timer-url="timer.php"></div>',
    ],
    ids=["href", "hx-get", "data-url"],
)
def test_a_relative_php_url_in_a_template_is_flagged(source):
    assert len(template_offenders(source)) == 1


@pytest.mark.parametrize(
    "source",
    [
        '<a href="/timer.php">Timer</a>',
        "<a href=\"{% url 'timer' %}\">Timer</a>",
        "{# PHP linked the relative timer.php. #}",
        '<!-- <a href="timer.php"> -->',
    ],
    ids=["root", "url-tag", "django-comment", "html-comment"],
)
def test_a_root_php_url_in_a_template_passes(source):
    assert template_offenders(source) == []
