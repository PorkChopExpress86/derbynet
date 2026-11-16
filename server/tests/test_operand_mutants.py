"""tools/operand_mutants.py lists the operand mutations of a diff's
`min()`, `max()`, `clamp()` and conditions (#283 retro)."""

import subprocess

import pytest

from tools import operand_mutants as om


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
    """#370's review: a set of 400 names listed 400 mutants. Over
    MAX_MEMBERS, the listing names the collection and lists none; at
    MAX_MEMBERS it lists each."""
    many = ", ".join(f'"n{i}"' for i in range(om.MAX_MEMBERS + 1))
    enough = ", ".join(f'"n{i}"' for i in range(om.MAX_MEMBERS))
    (tmp_path / "mod.py").write_text("")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "base"]):
        subprocess.run([*git, *args], cwd=tmp_path, check=True)
    (tmp_path / "mod.py").write_text(f"MANY = {{{many}}}\nENOUGH = {{{enough}}}\n")
    monkeypatch.chdir(tmp_path)
    assert om.main(["--base", "HEAD"]) == 0
    out, err = capsys.readouterr()
    assert [line.split("  ")[0] for line in out.splitlines()] == ["mod.py:2"] * (
        om.MAX_MEMBERS
    )
    assert err == (
        f"mod.py:1: {om.MAX_MEMBERS + 1} members, over {om.MAX_MEMBERS}: no member"
        " rows; take away by hand the ones a rule reads\n"
    )
