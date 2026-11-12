"""tools/operand_mutants.py lists the operand mutations of a diff's
`min()`, `max()`, `clamp()` and conditions (#283 retro)."""

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
