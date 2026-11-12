"""Prose that names a clamped font by its viewport term names the clamp.

A font declared `clamp(1rem, 2.2vh, 2rem)` is 2.2vh only between its
bounds. #283's README and
`kiosk.css` comments called the row font "2.2vh" with the clamp in the
implementer's own grep output, and its test file's comments kept "the
label's 2.2vh font" after review (#283 retro). This test reads each `font`
and `font-size` declaration with a `clamp()` in the port's stylesheets,
takes the viewport terms of its middle value (2.2vh; 9vh and 11vw of
`min(9vh, 11vw)`), and fails a line of a comment, docstring or README
that calls one of them a font ("the row's 2.2vh font", "font of 11vw", #287's
commit message's "font is its row's 2.2vh") with no `clamp(` on that
line. Name the clamp, or a declaration such as
`font-size: 2.2vh` that sets the term alone.
"""

import re
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
SKIP = {".venv", "node_modules", "vendor", "var", "__pycache__"}
FONT = re.compile(r"\bfont(?:-size)?\s*:\s*[^;{}]*\bclamp\(")
TERM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?v(?:h|w|min|max)\b")


def clamp_middles(css: str) -> set[str]:
    """The viewport terms of each font clamp's middle value."""
    terms = set()
    for m in FONT.finditer(css):
        start = m.end()
        depth, i, commas = 1, start, []
        while i < len(css) and depth:
            c = css[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == "," and depth == 1:
                commas.append(i)
            i += 1
        if len(commas) == 2:
            terms.update(TERM.findall(css[commas[0] + 1 : commas[1]]))
    return terms


def claims(text: str, terms: set[str]) -> list[tuple[int, str]]:
    """`(line, text)` of each line that calls a term a font without its
    clamp."""
    if not terms:
        return []
    alt = "|".join(re.escape(t) for t in sorted(terms))
    said = re.compile(
        rf"(?<![\w.])(?:{alt})(?:'s)?\s+font\b"
        rf"|\bfont(?:-size)?\b[^.;:\n]{{0,30}}?(?<![\w.])(?:{alt})(?![\w.])"
    )
    return [
        (n, line.strip())
        for n, line in enumerate(text.splitlines(), 1)
        if said.search(line) and "clamp(" not in line
    ]


def files(*suffixes):
    for path in sorted(SERVER.rglob("*")):
        if path.suffix not in suffixes or path == Path(__file__):
            continue
        if SKIP.intersection(path.relative_to(SERVER).parts[:-1]):
            continue
        yield path


def terms_in_stylesheets() -> set[str]:
    return set().union(*(clamp_middles(p.read_text()) for p in files(".css")))


def test_the_stylesheets_have_font_clamps():
    """The check is never vacuous: kiosk.css's body font is a clamp."""
    assert "2.2vh" in terms_in_stylesheets()


def test_no_prose_calls_a_clamped_font_by_its_term():
    terms = terms_in_stylesheets()
    found = [
        f"{path.relative_to(SERVER)}:{n}: {line}"
        for path in files(".py", ".js", ".html", ".md", ".css")
        for n, line in claims(path.read_text(), terms)
    ]
    assert found == [], (
        "These call a clamped font by its middle term; name the clamp "
        "(clamp(1rem, 2.2vh, 2rem)) or what it comes to at that size:\n"
        + "\n".join(found)
    )


def test_it_flags_283s_comments_and_passes_the_fixed_ones():
    css = "body { font: 400 clamp(1rem, 2.2vh, 2rem)/1.25 system-ui; }\n"
    css += ".t { font-size: clamp(1.75rem, min(9vh, 11vw), 8rem); }\n"
    css += ".k-row { font-size: 2.2vh; }\n"
    terms = clamp_middles(css)
    assert terms == {"2.2vh", "9vh", "11vw"}
    flagged = (
        "# Tall windows, where the label's 2.2vh font outgrew its 9vw column\n"
        "# The windows where the cap is smaller than the row's 2.2vh font, so it\n"
        "the title's font of 11vw\n"
        "From 641 px its font is its row's 2.2vh or a width cap\n"
    )
    assert [n for n, _ in claims(flagged, terms)] == [1, 2, 3, 4]
    kept = (
        "# the row's font, clamp(1rem, 2.2vh, 2rem), outgrew its column\n"
        ".k-row { font-size: 2.2vh; }\n"
        "a 12.2vh font\n"
        "2.2vw font\n"
    )
    assert claims(kept, terms) == []
