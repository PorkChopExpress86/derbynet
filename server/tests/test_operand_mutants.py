"""tools/operand_mutants.py lists the operand mutations of a diff's
`min()`, `max()`, `clamp()` and conditions (#283 retro)."""

import ast
import subprocess
from pathlib import Path

import pytest

from tools import operand_mutants as om

ROOT = Path(__file__).resolve().parents[2]


def listed(diff: str) -> list[tuple[int, str, str, str]]:
    return [(m.line, m.kind, m.old, m.new) for m in om.mutants(diff)]


def test_283s_caps_list_each_operand_alone():
    """`min(1em, 2.85vw)` replaced by `2.85vw` alone is the mutant #283's
    first rounds missed (r01 in #287)."""
    diff = (
        "+++ b/server/ui/static/ui/kiosk.css\n"
        "@@ -372,0 +373 @@\n"
        "+  .k-deck th:not(.k-divider) { padding: 0 .5vw; "
        "font-size: min(1em, 2.85vw); }\n"
    )
    assert listed(diff) == [
        (373, "min", "min(1em, 2.85vw)", "1em"),
        (373, "min", "min(1em, 2.85vw)", "2.85vw"),
    ]


def test_a_clamp_lists_each_bound_dropped_and_its_nested_call():
    diff = (
        "+++ b/a.css\n@@ -1 +1 @@\n"
        "+.t { font-size: clamp(1.75rem, min(9vh, 11vw), 8rem); }\n"
    )
    assert [m[3] for m in listed(diff)] == [
        "min(9vh, 11vw)",
        "max(1.75rem, min(9vh, 11vw))",
        "min(min(9vh, 11vw), 8rem)",
        "9vh",
        "11vw",
    ]


@pytest.mark.parametrize(
    ("call", "news"),
    [
        ("max(xs, key=f)", []),
        ("max(a, b, key=f)", ["a", "b"]),
        ("min(a, b)", ["a", "b"]),
        # A comma in the key's lambda splits no operand.
        ("max(xs, key=lambda a, b: a)", []),
        ("min(a, b, default=0)", ["a", "b"]),
        ("max(a, *xs)", []),
        ("max(a, b, *xs)", ["a", "b"]),
        ("max(a, b, **kw)", ["a", "b"]),
        ("min(a + b, c)", ["(a + b)", "c"]),
        ("min(a.b, c[0], f(d), 's', None)", ["a.b", "c[0]", "f(d)", "'s'", "None"]),
        ("max(1, [2])", ["(1)", "([2])"]),
    ],
)
def test_a_python_call_lists_only_its_positional_operands(call, news):
    """#417 retro's `max(holders, key=lambda node: node.lineno)` listed
    `key=lambda node: node.lineno`, and `return key=...` did not parse
    (#449)."""
    diff = f"+++ b/t.py\n@@ -1 +1 @@\n+x = {call}\n"
    assert [(m.kind, m.old, m.new) for m in om.mutants(diff)] == [
        (call[:3], call, new) for new in news
    ]


@pytest.mark.parametrize(
    ("path", "line", "olds"),
    [
        ("a.js", "x = Math.max(a, b);", ["Math.max(a, b)"] * 2),
        ("t.py", "x = self.items.max(a + b, c)", ["self.items.max(a + b, c)"] * 2),
        ("a.js", "x = f().max(a, b);", []),
        ("t.py", "x = xs[0].min(a, b)", []),
    ],
)
def test_a_call_on_a_receiver_is_replaced_with_its_receiver(path, line, olds):
    """`Math.max(a, b)` with `max(a, b)` replaced read `Math.a`, and
    `obj.max(a + b, c)` read `obj.(a + b)`, which does not parse (#449)."""
    diff = f"+++ b/{path}\n@@ -1 +1 @@\n+{line}\n"
    assert [m.old for m in om.mutants(diff)] == olds


def test_a_call_in_a_python_string_that_is_no_python_lists_each_operand():
    """CSS in a Python string, as a browser test keeps it."""
    diff = '+++ b/t.py\n@@ -1 +1 @@\n+CSS = ".t { width: min(1em, 2vw); }"\n'
    assert [m.new for m in om.mutants(diff)] == ["1em", "2vw"]


@pytest.mark.parametrize(
    ("line", "news"),
    [
        # race_day_screens.py's SCROLL_TO, as it reads there.
        ('S = "Math.max(0, ...stuck.map(x => x.top))"', []),
        ('S = "Math.max(0, ...[a, b])"', []),
        ('S = "Math.max(a, b, ...xs)"', ["a", "b"]),
    ],
)
def test_a_spread_in_a_python_string_is_no_operand(line, news):
    """JavaScript in a Python string, as `server/tools/race_day_screens.py`
    keeps it: `...stuck.map(...)` is no JavaScript expression, and its
    mutant would read as KILLED for want of parsing (#449). `...[a, b]`
    parses as a Python subscript of `...`, so it is skipped there too."""
    diff = f"+++ b/t.py\n@@ -1 +1 @@\n+{line}\n"
    assert [m.new for m in om.mutants(diff)] == news


@pytest.mark.parametrize("path", ["a.js", "t.py", "a.css"])
def test_a_call_right_after_a_minus_lists_nothing(path):
    """`CALL` skips a call after `-`, so `x-max(` in CSS is no call; so is
    `-max(a, b)` (#449)."""
    diff = f"+++ b/{path}\n@@ -1 +1 @@\n+x = -max(a, b)\n"
    assert om.mutants(diff) == []


@pytest.mark.parametrize(
    ("path", "line", "news"),
    [
        ("a.js", "x = Math.max(a, key = f);", ["a", "key = f"]),
        ("a.css", ".t { width: max(1em, 2vw); }", ["1em", "2vw"]),
        ("a.css", ".t { width: min(1em); }", []),
        ("a.css", ".t { width: clamp(1em, 2vw); }", []),
        ("a.js", "x = Math.max(0, ...xs);", []),
        ("a.js", "x = Math.max(a, b, ...xs);", ["a", "b"]),
    ],
)
def test_a_javascript_or_css_call_lists_each_operand_of_two_or_more(path, line, news):
    """Neither language has a named argument, so each operand counts:
    JavaScript's `key = f` is an assignment, which stands where the call
    stood (#449). A clamp() with no three operands lists nothing, and a
    spread is no operand."""
    diff = f"+++ b/{path}\n@@ -1 +1 @@\n+{line}\n"
    assert [m.new for m in om.mutants(diff)] == news


# One sample of each expression ast has, as a call takes it for an argument.
SAMPLES = [
    (ast.BoolOp, "a or b"),
    (ast.NamedExpr, "y := 1"),
    (ast.BinOp, "a + b"),
    (ast.UnaryOp, "-a"),
    (ast.Lambda, "lambda p, q=1: p"),
    (ast.IfExp, "a if c else b"),
    (ast.Dict, "{1: 2, **d}"),
    (ast.Set, "{1, 2}"),
    (ast.ListComp, "[i for i in xs]"),
    (ast.SetComp, "{i for i in xs}"),
    (ast.DictComp, "{i: j for i, j in xs}"),
    (ast.GeneratorExp, "(i for i in xs)"),
    (ast.Await, "await a"),
    (ast.Yield, "(yield a)"),
    (ast.YieldFrom, "(yield from xs)"),
    (ast.Compare, "a < b < c"),
    (ast.Call, "f(a, *b, k=1)"),
    (ast.JoinedStr, 'f"{a!r:>{w}}, b"'),
    (ast.TemplateStr, 't"{a}"'),
    (ast.Constant, "1"),
    (ast.Constant, "1.5"),
    (ast.Constant, "'s, t'"),
    (ast.Constant, "'s' 't'"),
    (ast.Constant, "None"),
    (ast.Constant, "..."),
    (ast.Attribute, "a.b"),
    (ast.Subscript, "a[1:2, ::3]"),
    (ast.Name, "a"),
    (ast.List, "[1, *xs]"),
    (ast.Tuple, "(1, 2)"),
]
NO_ARGUMENT = {
    ast.Starred: "each argument list below takes `*xs`",
    ast.Slice: "only in a subscript",
    ast.FormattedValue: "only in an f-string",
    ast.Interpolation: "only in a t-string",
}
ARGUMENT_LISTS = [
    "{s}, z",
    "z, {s}",
    "z, {s}, w",
    "{s}, key=f",
    "{s}, z, key=lambda p, q: p",
    "{s}, *zs",
    "{s}, z, *zs",
    "*zs, {s}, z",
    "{s}, z, **kw",
]
# CALL takes `-max(` for a CSS name such as `x-max(`, so the minus is spaced.
HOSTS = [
    "x = {}",
    "x = - {}",
    "x = {} ** 2",
    "x = {}.real",
    "x = {}[0]",
    "x = {}(0)",
    "x = g({})",
    "x = obj.{}",
    "x = obj.items.{}",
]


def test_the_samples_hold_each_expression_ast_has():
    assert {cls for cls, _ in SAMPLES} | set(NO_ARGUMENT) == set(
        ast.expr.__subclasses__()
    )
    for cls, sample in SAMPLES:
        assert type(ast.parse(f"f({sample})", mode="eval").body.args[0]) is cls


def is_max(node: ast.AST) -> bool:
    """`max(...)`, or `max` called on a receiver."""
    return isinstance(node, ast.Call) and (
        getattr(node.func, "id", None) == "max"
        or getattr(node.func, "attr", None) == "max"
    )


class Stand(ast.NodeTransformer):
    """The tree with the `max()` call replaced by one of its arguments."""

    def __init__(self, arg: ast.expr):
        self.arg = arg

    def visit_Call(self, node):
        if is_max(node):
            return self.arg
        return self.generic_visit(node)


def test_each_python_operand_stands_where_the_call_stood():
    """Python's own parser is the oracle (#449): for each expression
    form, at each place in an argument list beside a keyword, `*` and
    `**`, under each host, the listing offers exactly the call's
    positional arguments when it has two or more, and the line with the
    call replaced by each parses to the tree with the call replaced by
    that argument. An argument led by `...` is none, since no max() can
    order an Ellipsis (a spread has its own test)."""
    wrong = []
    for _, sample in SAMPLES:
        for args in ARGUMENT_LISTS:
            call = "max(" + args.format(s=sample) + ")"
            for host in HOSTS:
                line = host.format(call)
                tree = ast.parse(line)
                node = next(n for n in ast.walk(tree) if is_max(n))
                operands = [
                    a
                    for a in node.args
                    if not isinstance(a, ast.Starred)
                    and not ast.get_source_segment(line, a).startswith("...")
                ]
                expected = operands if len(operands) > 1 else []
                found = [
                    m for m in om.line_mutants("t.py", line, True) if m[0] == "max"
                ]
                if len(found) != len(expected):
                    wrong.append((line, [m[2] for m in found]))
                    continue
                for (_, old, new), arg in zip(found, expected, strict=True):
                    mutated = line.replace(old, new)
                    want = ast.dump(Stand(arg).visit(ast.parse(line)))
                    try:
                        got = ast.dump(ast.parse(mutated))
                    except SyntaxError:
                        got = "SyntaxError"
                    if old != ast.get_source_segment(line, node) or got != want:
                        wrong.append((line, mutated))
    assert wrong == [], f"{len(wrong)} wrong, first: {wrong[:5]}"


def test_conditions_and_each_top_level_operand():
    diff = (
        "+++ b/t.py\n"
        "@@ -10,0 +11,3 @@\n"
        "+    held = [x for x in labels if rows[x](1, 2)]\n"
        "+    if a and (b or c):\n"
        "+  const rows = [...q()].filter(tr => !tr.x && tr.id);\n"
    )
    assert listed(diff) == [
        (11, "condition", "rows[x](1, 2)", "True"),
        (11, "condition", "rows[x](1, 2)", "False"),
        (12, "condition", "a and (b or c)", "True"),
        (12, "condition", "a and (b or c)", "False"),
        (12, "operand", "a", "True"),
        (12, "operand", "a", "False"),
        (12, "operand", "(b or c)", "True"),
        (12, "operand", "(b or c)", "False"),
        (13, "filter", "tr => !tr.x && tr.id", "tr => true"),
        (13, "filter", "tr => !tr.x && tr.id", "tr => false"),
        (13, "operand", "!tr.x", "true"),
        (13, "operand", "!tr.x", "false"),
        (13, "operand", "tr.id", "true"),
        (13, "operand", "tr.id", "false"),
    ]


def test_a_javascript_listener_is_renamed_so_it_never_fires():
    """#344's review found an untested pointercancel listener, and guards
    that htmx made moot survived round after round (#344 retro)."""
    diff = (
        "+++ b/server/ui/static/ui/kiosks.js\n"
        "@@ -1,0 +1,4 @@\n"
        '+board.addEventListener("htmx:beforeSwap", (e) => {\n'
        "+  htmx.on('htmx:afterRequest', f);\n"
        "+  el.addEventListener(`pointercancel`, g, {once: true});\n"
        "+  x.on(name, g);\n"
    )
    assert listed(diff) == [
        (1, "listener", '"htmx:beforeSwap"', '"never-htmx:beforeSwap"'),
        (2, "listener", "'htmx:afterRequest'", "'never-htmx:afterRequest'"),
        (3, "listener", "`pointercancel`", "`never-pointercancel`"),
    ]


def test_a_python_on_call_is_no_listener():
    """Playwright's `page.on("request", ...)` in a test helper is Python."""
    diff = (
        '+++ b/ui/tests/browser/test_x.py\n@@ -1,0 +1 @@\n+page.on("request", seen)\n'
    )
    assert listed(diff) == []


def test_each_old_text_is_on_its_line_and_removed_lines_are_skipped():
    diff = (
        "+++ b/x.js\n"
        "@@ -1,2 +1,2 @@\n"
        "-if (old) {}\n"
        " keep();\n"
        "+if (x > Math.max(a, b)) {}\n"
    )
    found = om.mutants(diff)
    assert {m.line for m in found} == {2}
    for m in found:
        assert m.old in "if (x > Math.max(a, b)) {}"


def test_a_python_script_with_no_extension_is_read_as_python(tmp_path):
    """#320's `plan/bin/affected-tests` has no `.py`: the tool read it as
    JavaScript, listed `if (module, name) in seen:` as a condition of
    `module, name`, and missed each Python `if`, so every condition was
    listed by hand (#320 retro). Its shebang names Python."""
    script = tmp_path / "plan" / "bin" / "tool"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env python3\nif (module, name) in seen:\n")
    hook = "#! /bin/bash\nif (x) {}\necho [n for n in seen if n]\n"
    (tmp_path / "plan" / "bin" / "hook").write_text(hook)
    diff = (
        "+++ b/plan/bin/tool\n@@ -1,0 +2 @@\n+if (module, name) in seen:\n"
        "+++ b/plan/bin/hook\n@@ -1,0 +2,2 @@\n"
        "+if (x) {}\n+echo [n for n in seen if n]\n"
    )
    found = [(m.path, m.old, m.new) for m in om.mutants(diff, tmp_path)]
    assert found == [
        ("plan/bin/tool", "(module, name) in seen", "True"),
        ("plan/bin/tool", "(module, name) in seen", "False"),
        ("plan/bin/hook", "x", "true"),
        ("plan/bin/hook", "x", "false"),
    ]
    # With no tree to read, a file with no extension is no Python.
    assert [m.new for m in om.mutants(diff)][:2] == ["true", "false"]


def test_javascript_in_a_python_if_keeps_its_mutants():
    """A browser test's `if page.evaluate("...")` line holds JavaScript in a
    string: the Python condition is listed, and the JavaScript filter's
    mutants too, but not the Python `if` read as JavaScript (#340 review)."""
    line = 'if page.evaluate("() => items.filter(x => x.a && x.b).length"):'
    diff = f"+++ b/t.py\n@@ -1,0 +1 @@\n+{line}\n"
    found = [(m.kind, m.old, m.new) for m in om.mutants(diff)]
    call = 'page.evaluate("() => items.filter(x => x.a && x.b).length")'
    assert found == [
        ("condition", call, "True"),
        ("condition", call, "False"),
        ("filter", "x => x.a && x.b", "x => true"),
        ("filter", "x => x.a && x.b", "x => false"),
        ("operand", "x.a", "true"),
        ("operand", "x.a", "false"),
        ("operand", "x.b", "true"),
        ("operand", "x.b", "false"),
    ]


def added_at(path: str, needle: str) -> str:
    """A diff that adds the one line of the repository's `path` holding
    `needle`."""
    lines = om.read(ROOT / path).split("\n")
    [n] = [i for i, line in enumerate(lines, 1) if needle in line]
    return f"+++ b/{path}\n@@ -{n - 1},0 +{n} @@\n+{lines[n - 1]}\n"


def test_a_comprehension_s_if_is_python_and_a_browser_test_s_string_javascript():
    """heats.py's `next((n for n, r, h, _ in index if (r, h) == current), 0)`
    listed `r, h -> true` and `r, h -> false`, JavaScript spliced into
    Python, which `mutate.py --listed` refused (#468). Its comprehension's
    condition is listed by AST only, and a browser test's JavaScript `if (`
    in a string keeps its mutants."""
    heats = added_at("server/racing/services/heats.py", "index if (r, h) == current")
    assert [(m.kind, m.old, m.new) for m in om.mutants(heats, ROOT)] == [
        ("condition", "(r, h) == current", "True"),
        ("condition", "(r, h) == current", "False"),
    ]
    slideshow = added_at(
        "server/ui/tests/browser/test_kiosk_slideshow.py", "slides at the change"
    )
    assert [(m.kind, m.old, m.new) for m in om.mutants(slideshow, ROOT)] == [
        ("condition", "slides.length !== 1", "true"),
        ("condition", "slides.length !== 1", "false"),
    ]


def test_a_javascript_if_in_a_python_file_is_read_only_inside_a_string(tmp_path):
    """By AST, a JavaScript `if (` in a Python file is listed inside a
    string, a triple-quoted one's inner line, an f-string's text and a
    bytes literal too, and not in code: a comprehension's `if (`, a Python
    `if (`, a comment outside a literal, an f-string's `{...}` field, or a
    ternary's `if (` right after a string's closing quote (#468). ast's
    columns count UTF-8 bytes, so the `é`s before line 7's string leave it
    holding its `if (`. A line the diff adds past the file's end is read by
    line, as with no tree."""
    text = (
        "seen = [n for n in xs if (n, 1) in done]\n"
        "if (a, b) in seen:  # if (c) in a comment\n"
        '    é = page.evaluate("() => { if (ok && ready) go(); }")\n'
        'JS = """\n'
        "  if (é && b) stop();\n"
        '"""\n'
        'ééééé = "if (k) x"\n'
        'y = f"{z if (q) else w} if (v) run()"\n'
        'raw = b"if (u) go()"\n'
        'y2 = "x"if (t) else "z"\n'
    )
    (tmp_path / "t.py").write_text(text)
    diff = "+++ b/t.py\n@@ -0,0 +1,10 @@\n" + "".join(
        f"+{line}\n" for line in text.splitlines()
    )
    diff += "@@ -11,0 +12 @@\n+if (late) {}\n"
    found = om.mutants(diff, tmp_path)
    javascript = [(m.line, m.old, m.new) for m in found if m.new in ("true", "false")]
    assert javascript == [
        (3, "ok && ready", "true"),
        (3, "ok && ready", "false"),
        (3, "ok", "true"),
        (3, "ok", "false"),
        (3, "ready", "true"),
        (3, "ready", "false"),
        (5, "é && b", "true"),
        (5, "é && b", "false"),
        (5, "é", "true"),
        (5, "é", "false"),
        (5, "b", "true"),
        (5, "b", "false"),
        (7, "k", "true"),
        (7, "k", "false"),
        (8, "v", "true"),
        (8, "v", "false"),
        (9, "u", "true"),
        (9, "u", "false"),
        (12, "late", "true"),
        (12, "late", "false"),
    ]
    # The Python conditions are still listed, by AST.
    python = {(m.line, m.old) for m in found if m.new in ("True", "False")}
    assert python == {
        (1, "(n, 1) in done"),
        (2, "(a, b) in seen"),
        (8, "q"),
        (10, "t"),
    }


def test_the_string_rule_s_limits_a_concatenation_s_comment_and_no_tree(tmp_path):
    """A comment between the parts of an implicitly concatenated string
    sits inside the literal's span, so its `if (` is listed as JavaScript.
    A file with no tree keeps the line rule, so a comprehension's `if (`
    is listed as JavaScript beside its Python condition (#468)."""
    (tmp_path / "cat.py").write_text('x = ("a"  # if (c) go\n     "b")\n')
    diff = '+++ b/cat.py\n@@ -0,0 +1 @@\n+x = ("a"  # if (c) go\n'
    assert [(m.line, m.kind, m.old, m.new) for m in om.mutants(diff, tmp_path)] == [
        (1, "condition", "c", "true"),
        (1, "condition", "c", "false"),
    ]
    line = "seen = [n for n in xs if (n, 1) in done]"
    (tmp_path / "bad.py").write_text(f"{line}\ndef (:\n")
    diff = f"+++ b/bad.py\n@@ -0,0 +1 @@\n+{line}\n"
    assert [(m.line, m.kind, m.old, m.new) for m in om.mutants(diff, tmp_path)] == [
        (1, "condition", "(n, 1) in done", "True"),
        (1, "condition", "(n, 1) in done", "False"),
        (1, "condition", "n, 1", "true"),
        (1, "condition", "n, 1", "false"),
    ]


def test_a_python_file_on_disk_lists_every_condition_and_operand_by_ast(tmp_path):
    """#336's round-1 survivors sat in a `return a and b` split over lines
    (`is_docstring`), a multi-line `if not (...)` (`marks_only`) and an
    `or` inside `any(...)` (`reads_doc`), which the line reader skips, so
    they were listed by hand, and some never (#336 retro). By AST each is
    listed with the span `tools/mutate.py --listed` replaces."""
    text = (
        "def is_doc(node):\n"
        "    return isinstance(node, Expr) and isinstance(node.value, Constant) \\\n"
        "        and isinstance(node.value.value, str)\n"
        "\n"
        "\n"
        "def old(a, b):\n"
        "    return a if a and b else b\n"
        "\n"
        "\n"
        "def wait(q):\n"
        "    while q and not (q[0] or\n"
        "                     q[1]):\n"
        "        q.pop()\n"
        "    return 1 if q else 0\n"
        "    return [x for x in q if x]\n"
        "    while True:\n"
        "        break\n"
    )
    (tmp_path / "t.py").write_text(text)
    lines = text.splitlines()
    diff = "+++ b/t.py\n@@ -0,0 +1,3 @@\n" + "".join(f"+{x}\n" for x in lines[:3])
    diff += "@@ -9,0 +10,8 @@\n" + "".join(f"+{x}\n" for x in lines[9:])
    found = om.mutants(diff, tmp_path)
    for m in found:
        assert text[m.span[0] : m.span[1]] == m.old, m
    # `while True` made true changes nothing, so only its False is listed.
    assert [(m.kind, m.old, m.new) for m in found if m.line == 16] == [
        ("condition", "True", "False")
    ]
    found = [m for m in found if m.line != 16]
    assert [(m.line, m.kind, m.old, m.new) for m in found][::2] == [
        (2, "operand", "isinstance(node, Expr)", "True"),
        (2, "operand", "isinstance(node.value, Constant)", "True"),
        (3, "operand", "isinstance(node.value.value, str)", "True"),
        (11, "condition", "q and not (q[0] or\n                     q[1])", "True"),
        (11, "operand", "q", "True"),
        (11, "operand", "not (q[0] or\n                     q[1])", "True"),
        (11, "operand", "q[0]", "True"),
        (12, "operand", "q[1]", "True"),
        (14, "condition", "q", "True"),
        (15, "condition", "x", "True"),
    ]
    assert {m.new for m in found[1::2]} == {"False"}
    assert str(found[6]) == "t.py:11  condition  q and not (q[0] or q[1])  ->  True"
    assert 7 not in {m.line for m in found}, "line 7's condition is on no added line"


def operand_mutants_of(tmp_path, path: str) -> set[tuple[int, str, str]]:
    """The operand mutants of a test with asserts and an `if`, every line
    added, written at `path`."""
    text = (
        "def test_x(a, b, c):\n"
        "    assert a and (b or c)\n"
        "    if a and b:\n"
        "        pass\n"
        "    assert a or c\n"
    )
    (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / path).write_text(text)
    lines = text.splitlines()
    diff = f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
    diff += "".join(f"+{x}\n" for x in lines)
    found = om.mutants(diff, tmp_path)
    return {(m.line, m.old, m.new) for m in found if m.kind == "operand"}


def every_operand() -> set[tuple[int, str, str]]:
    """operand_mutants_of()'s operands, each made True and made False."""
    olds = {2: ("a", "b or c", "b", "c"), 3: ("a", "b"), 5: ("a", "c")}
    return {
        (line, old, new)
        for line, names in olds.items()
        for old in names
        for new in ("True", "False")
    }


@pytest.mark.parametrize(
    "path",
    [
        "test_t.py",
        "app/t_test.py",
        "app/conftest.py",
        "app/tests/replay.py",
    ],
)
def test_a_test_module_s_assert_and_operand_is_made_false_only(tmp_path, path):
    """In a passing test each operand of an assert's `and` is true, so made
    True it changes nothing: #500's `assert raced.exists() and
    unraced.exists()` and `assert racers and heats` listed such mutants,
    which could not die (#500 retro). An `or` inside the `and`, an
    assert's own `or`, and an `and` in an `if` keep both."""
    assert operand_mutants_of(tmp_path, path) == every_operand() - {
        (2, "a", "True"),
        (2, "b or c", "True"),
    }


@pytest.mark.parametrize("path", ["tools/race_day_screens.py", "app/testing.py"])
def test_an_assert_outside_the_tests_keeps_both(tmp_path, path):
    """A tool's assert can fail, as race_day_screens.py's check of a reply
    can, so each operand made True can die (PR #517's review)."""
    assert operand_mutants_of(tmp_path, path) == every_operand()


def test_a_line_mutant_on_disk_has_its_span_where_its_text_is_once(tmp_path):
    """--listed replaces a line mutant at its span: none when its text is
    on its line twice, or when the diff names a line past the file's end,
    and a stylesheet comment's call is no mutant. A file that is no Python
    is read by line, even where its text parses as Python."""
    css = (
        ".a { font-size: min(1em, 2vw); }\n"
        "/* was min(1em, 3vw) */ .b { width: max(1px, 2px) max(1px, 2px); }\n"
    )
    (tmp_path / "a.css").write_text(css)
    (tmp_path / "notes.txt").write_text("seen = [n for n in seen if n]\n")
    diff = "+++ b/a.css\n@@ -0,0 +1,2 @@\n" + "".join(
        f"+{x}\n" for x in css.splitlines()
    )
    diff += "@@ -2,0 +5 @@\n+.c { height: min(1vh, 2vh); }\n"
    diff += "+++ b/notes.txt\n@@ -0,0 +1 @@\n+seen = [n for n in seen if n]\n"
    found = om.mutants(diff, tmp_path)
    assert [(m.line, m.old, m.new) for m in found] == [
        (1, "min(1em, 2vw)", "1em"),
        (1, "min(1em, 2vw)", "2vw"),
        (2, "max(1px, 2px)", "1px"),
        (2, "max(1px, 2px)", "2px"),
        (2, "max(1px, 2px)", "1px"),
        (2, "max(1px, 2px)", "2px"),
        (5, "min(1vh, 2vh)", "1vh"),
        (5, "min(1vh, 2vh)", "2vh"),
    ]
    for m in found[:2]:
        assert css[m.span[0] : m.span[1]] == "min(1em, 2vw)"
    assert [m.span for m in found[2:]] == [None] * 6


def test_a_python_file_that_does_not_parse_is_read_by_line(tmp_path):
    (tmp_path / "t.py").write_text("if a and b:\n")
    diff = "+++ b/t.py\n@@ -0,0 +1 @@\n+if a and b:\n"
    assert [m.old for m in om.mutants(diff, tmp_path)] == [
        "a and b",
        "a and b",
        "a",
        "a",
        "b",
        "b",
    ]


def test_a_crlf_file_s_spans_are_its_own(tmp_path):
    """read_text() turned CRLF into LF, so every span fell short by a
    character a line, and --listed refused each mutant (#358 review)."""
    text = "x = 1\r\ny = 2\r\nif x and y:\r\n    w = min(x, y)\r\n"
    (tmp_path / "t.py").write_bytes(text.encode())
    diff = "+++ b/t.py\n@@ -2,0 +3,2 @@\n+if x and y:\n+    w = min(x, y)\n"
    found = om.mutants(diff, tmp_path)
    assert [(m.line, m.old) for m in found] == [
        (3, "x and y"),
        (3, "x and y"),
        (3, "x"),
        (3, "x"),
        (3, "y"),
        (3, "y"),
        (4, "min(x, y)"),
        (4, "min(x, y)"),
    ]
    for m in found:
        assert text[m.span[0] : m.span[1]] == m.old, m


def test_a_case_guard_is_a_condition(tmp_path):
    """`case _:` has no guard, and lists nothing."""
    text = (
        "match v:\n    case [a, b] if a and b:\n        pass\n"
        "    case _:\n        pass\n"
    )
    (tmp_path / "t.py").write_text(text)
    diff = "+++ b/t.py\n@@ -0,0 +1,5 @@\n" + "".join(
        f"+{x}\n" for x in text.splitlines()
    )
    found = om.mutants(diff, tmp_path)
    assert [(m.kind, m.old, m.new) for m in found][:2] == [
        ("condition", "a and b", "True"),
        ("condition", "a and b", "False"),
    ]
    assert {m.line for m in found} == {2}


def test_a_for_loop_lists_a_body_that_never_runs(tmp_path):
    """#345's scanner walked a command's continued lines in a loop, and
    `end += 1` made `break` survived review: no stand-in needed one pass
    (#345 retro). A `while` gets that mutant as its condition made False;
    a `for` loop gets its iterable made empty. A loop the diff leaves alone
    lists nothing."""
    text = (
        "def ends(lines):\n"
        "    for line in lines:\n"
        "        pass\n"
        "    async def a(xs):\n"
        "        async for x in (\n"
        "            xs\n"
        "        ):\n"
        "            pass\n"
        "    for kept in lines:\n"
        "        pass\n"
    )
    (tmp_path / "t.py").write_text(text)
    lines = text.splitlines()
    diff = "+++ b/t.py\n@@ -0,0 +1,8 @@\n" + "".join(f"+{x}\n" for x in lines[:8])
    found = om.mutants(diff, tmp_path)
    assert [(m.line, m.kind, m.old, m.new) for m in found] == [
        (2, "loop", "lines", "()"),
        (6, "loop", "xs", "()"),
    ]
    for m in found:
        assert text[m.span[0] : m.span[1]] == m.old, m


def test_a_loop_whose_header_is_unchanged_lists_nothing(tmp_path):
    """Only the body changed: the loop is older than the diff."""
    text = "for line in lines:\n    print(line)\n"
    (tmp_path / "t.py").write_text(text)
    diff = "+++ b/t.py\n@@ -2,0 +2,1 @@\n+    print(line)\n"
    assert om.mutants(diff, tmp_path) == []


def test_the_listing_reads_the_diff_from_the_merge_base(tmp_path, monkeypatch, capsys):
    """#348's listing, run after master moved, held master's own lines: from
    BASE itself, a line master changed after the branch left it reads as
    added (#348 retro)."""
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]

    def run(*args):
        return subprocess.run(
            [*git, *args], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()

    (tmp_path / "mod.py").write_text("def f(a, b):\n    if a < b:\n        return 1\n")
    run("init", "-q")
    run("add", ".")
    run("commit", "-qm", "base")
    master = run("branch", "--show-current")
    run("checkout", "-qb", "branch")
    with open(tmp_path / "mod.py", "a") as f:
        f.write("    if b:\n        return 2\n")
    run("commit", "-qam", "the branch's condition")
    run("checkout", "-q", master)
    (tmp_path / "mod.py").write_text("def f(a, b):\n    if a <= b:\n        return 1\n")
    run("commit", "-qam", "master moves on")
    run("checkout", "-q", "branch")
    monkeypatch.chdir(tmp_path)
    assert om.main(["--base", master]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "mod.py:4  condition  b  ->  True",
        "mod.py:4  condition  b  ->  False",
    ]


def test_each_member_of_a_set_and_of_an_in_tuple_is_taken_away(tmp_path):
    """#356's review took `"range"` out of LOOKUPS and `"values"` out of
    `("values_list", "values")`; both survived, and no listing had them
    (#356 retro). A tuple no `in` tests, one an `==` tests, a name an `in`
    tests, a one-member tuple, a tuple with a starred member (`(*PAIR)`
    does not parse) and an unchanged line list none."""
    text = (
        'LOOKUPS = frozenset({"gt", "range"})\n'
        "def f(attr, lookup, kind):\n"
        '    return attr in ("values_list", "values") or lookup not in [1, 2,]\n'
        'PAIR = ("a", "b")\n'
        'ONE = attr in ("a",)\n'
        'SAME = kind == ("a", "b") or kind in LOOKUPS\n'
        'STAR = kind in (*PAIR, "c")\n'
        'OLD = {"x", "y"}\n'
    )
    (tmp_path / "t.py").write_text(text)
    diff = "+++ b/t.py\n@@ -0,0 +1,7 @@\n" + "".join(
        f"+{x}\n" for x in text.splitlines()[:7]
    )
    found = [m for m in om.mutants(diff, tmp_path) if m.kind == "member"]
    assert [(m.line, m.old, m.new) for m in found] == [
        (1, '"gt", ', ""),
        (1, ', "range"', ""),
        (3, '"values_list", "values"', '"values",'),
        (3, '"values_list", "values"', '"values_list",'),
        (3, "1, ", ""),
        (3, ", 2", ""),
    ]
    for m in found:
        assert text[m.span[0] : m.span[1]] == m.old, m
        mutated = text[: m.span[0]] + m.new + text[m.span[1] :]
        compile(mutated, "t.py", "exec")


@pytest.mark.parametrize(
    "collection",
    ['("values_list", "values")', '("a", "b",)', '("a", "b", "c")', '["a", "b"]'],
)
def test_a_member_taken_away_leaves_the_same_kind_of_collection(tmp_path, collection):
    """#370's review: `("values_list", "values")` less `, "values"` was
    `("values_list")`, a string, and `in` on it a substring test, so the
    mutant survived a test that asserts `"values"` is refused. Each mutant
    evaluates to the type it started as, one member shorter."""
    text = f"KIND = x in {collection}\n"
    (tmp_path / "t.py").write_text(text)
    found = om.mutants(f"+++ b/t.py\n@@ -0,0 +1 @@\n+{text}", tmp_path)
    start = text.index(collection)
    whole = eval(collection)
    assert len(found) == len(whole)
    for m in found:
        mutated = text[: m.span[0]] + m.new + text[m.span[1] :]
        value = eval(mutated[start:].rstrip("\n"))
        assert type(value) is type(whole), (m, value)
        assert len(value) == len(whole) - 1, m


def test_a_crowded_collection_lists_no_members_and_is_named(
    tmp_path, monkeypatch, capsys
):
    """#370's review: a set of 400 names listed 400 mutants. Over 20
    members, the listing names the collection and lists none; at 20 it
    lists each. An unchanged crowded set, a deleted file and a script that
    is not Python name nothing."""
    many = ", ".join(f'"n{i}"' for i in range(21))
    enough = ", ".join(f'"n{i}"' for i in range(20))
    (tmp_path / "mod.py").write_text(f"OLD = {{{many}}}\n")
    (tmp_path / "gone.py").write_text("x = 1\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "base"]):
        subprocess.run([*git, *args], cwd=tmp_path, check=True)
    (tmp_path / "gone.py").unlink()
    (tmp_path / "mod.py").write_text(
        f"OLD = {{{many}}}\nMANY = x in ({many})\nENOUGH = {{{enough}}}\n"
    )
    (tmp_path / "page.js").write_text(f"S = {{{many}}}\n")
    subprocess.run([*git, "add", "page.js"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert om.main(["--base", "HEAD"]) == 0
    out, err = capsys.readouterr()
    assert [line.split("  ")[0] for line in out.splitlines()] == ["mod.py:3"] * 20
    assert err == (
        "mod.py:2: 21 members, over 20: no member rows; take away by hand the"
        " ones a rule reads\n"
    )


def test_a_file_the_diff_names_but_the_tree_lacks_is_not_crowded(tmp_path):
    """A diff read from stdin can name a file the tree does not hold."""
    many = ", ".join(f'"n{i}"' for i in range(21))
    diff = f"+++ b/gone.py\n@@ -0,0 +1 @@\n+S = {{{many}}}\n"
    assert om.crowded_in(diff, tmp_path) == []


def test_a_call_below_an_early_return_is_moved_above_it(tmp_path):
    """#383's `carryHeldKeys()` sat below `if (!opener) return;`, and
    review's hand mutant that moved it above the guard survived; no listed
    mutant moved it (#383 retro). A call below a guard that returns on
    another line, or below no guard, moves nowhere."""
    js = (
        "close() {\n"
        "  if (!opener) return;\n"
        "  carryHeldKeys();\n"
        "  if (!x) {\n"
        "    return;\n"
        "  }\n"
        "  later();\n"
        "  if (y) return 1;\n"
        "  const z = f();\n"
        "}\n"
    )
    (tmp_path / "app.js").write_text(js)
    diff = "+++ b/app.js\n@@ -0,0 +1,10 @@\n" + "".join(
        f"+{x}\n" for x in js.splitlines()
    )
    moved = [m for m in om.mutants(diff, tmp_path) if m.kind == "moved"]
    guard, call = "  if (!opener) return;", "  carryHeldKeys();"
    assert [(m.line, m.old, m.new) for m in moved] == [
        (3, f"{guard}\n{call}", f"{call}\n{guard}")
    ]
    assert js[moved[0].span[0] : moved[0].span[1]] == f"{guard}\n{call}"
    assert str(moved[0]) == (
        f"app.js:3  moved  if (!opener) return; carryHeldKeys();  ->  {call} {guard}"
    )


def test_a_call_on_the_first_line_or_past_the_end_moves_nowhere(tmp_path):
    """The first line has no line above it, though the file's last is a
    guard, and a line the diff names past the file's end has none."""
    js = "f();\nif (!x) return;"
    (tmp_path / "a.js").write_text(js)
    diff = "+++ b/a.js\n@@ -0,0 +1 @@\n+f();\n@@ -2,0 +3 @@\n+g();\n"
    assert [m for m in om.mutants(diff, tmp_path) if m.kind == "moved"] == []


@pytest.mark.parametrize("name", ["notes.md", "notes.txt", "test_page.py"])
def test_a_guard_and_call_outside_a_js_file_move_nowhere(tmp_path, name):
    """A Markdown or text file quotes such lines, and a Python string's
    JavaScript is left to hand mutants."""
    text = "  if (!opener) return;\n  carryHeldKeys();\n"
    (tmp_path / name).write_text(text)
    diff = f"+++ b/{name}\n@@ -0,0 +1,2 @@\n" + "".join(
        f"+{x}\n" for x in text.splitlines()
    )
    assert [m for m in om.mutants(diff, tmp_path) if m.kind == "moved"] == []
