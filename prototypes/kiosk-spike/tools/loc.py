#!/usr/bin/env python3
"""SPIKE #71: lines of code per page and candidate. Counts non-blank lines
that are not only a comment (docstrings excluded); Python counts only the
functions each page uses. Vendored libraries and the build output are not
counted; their sizes are reported separately.

    python3 -I tools/loc.py
"""

import ast
import gzip
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
COMMENT = ("//", "#", "{#", "/*", "*", "<!--")

PAGES = {
    "A": {
        "now-racing": {
            "files": [
                "spike/templates/spike/a/now_racing.html",
                "spike/templates/spike/a/_board.html",
                "spike/static/spike/a/now-racing.js",
                "spike/static/spike/a/now-racing.css",
            ],
            "py": {
                "views": ["a_now_racing", "a_now_racing_board"],
                "data": ["now_racing", "_heat_board", "now_racing_board"],
            },
        },
        "standings": {
            "files": [
                "spike/templates/spike/a/standings.html",
                "spike/templates/spike/a/_standings_table.html",
                "spike/static/spike/a/standings.js",
                "spike/static/spike/a/standings.css",
            ],
            "py": {
                "views": ["a_standings", "a_kiosk_poll", "a_standings_table"],
                "data": ["standings", "_avg"],
            },
        },
        "leaders": {
            "files": [
                "spike/templates/spike/a/leaders.html",
                "spike/templates/spike/a/_leaders_panel.html",
                "spike/static/spike/a/leaders.js",
                "spike/static/spike/a/leaders.css",
            ],
            "py": {"views": ["a_leaders", "a_leaders_panel"], "data": ["leaders"]},
        },
        "shared": {
            "files": [
                "spike/templates/spike/a/base.html",
                "spike/static/spike/a/common.js",
                "spike/static/spike/a/common.css",
            ],
            "py": {},
        },
    },
    "B": {
        "now-racing": {
            "files": ["svelte/now-racing.html", "svelte/src/NowRacing.svelte"],
            "py": {},
        },
        "standings": {
            "files": ["svelte/standings.html", "svelte/src/Standings.svelte"],
            "py": {"views": ["api_standings"], "data": ["standings", "_avg"]},
        },
        "leaders": {
            "files": ["svelte/leaders.html", "svelte/src/Leaders.svelte"],
            "py": {"views": ["api_leaders"], "data": ["leaders"]},
        },
        "shared": {
            "files": ["svelte/package.json", "svelte/vite.config.js"],
            "py": {"views": ["b_static"]},
        },
    },
    "both": {
        "shared": {
            "files": [
                "spike/static/spike/kiosk.css",
                "spike/static/spike/spike-common.js",
            ],
            "py": {"data": ["active_event"]},
        },
        "push (#56)": {
            "files": [],
            "py": {"views": ["events"], "data": ["change_signature"]},
        },
    },
}


def count_text(lines) -> int:
    return sum(
        1 for line in lines if line.strip() and not line.strip().startswith(COMMENT)
    )


def count_functions(module: str, names) -> int:
    source = (HERE / "spike" / f"{module}.py").read_text()
    lines = source.splitlines()
    total = 0
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            body = lines[node.lineno - 1 - len(node.decorator_list) : node.end_lineno]
            doc = ast.get_docstring(node, clean=False)
            skip = 0
            if doc is not None:
                first = node.body[0]
                skip = first.end_lineno - first.lineno + 1
            total += count_text(body) - skip
    return total


def size(path: Path) -> dict:
    data = path.read_bytes()
    return {"bytes": len(data), "gzip": len(gzip.compress(data))}


def main():
    table = {}
    for cand, pages in PAGES.items():
        for page, parts in pages.items():
            front = sum(
                count_text((HERE / f).read_text().splitlines()) for f in parts["files"]
            )
            py = sum(count_functions(m, names) for m, names in parts["py"].items())
            table[f"{cand} {page}"] = {
                "front_end": front,
                "python": py,
                "total": front + py,
            }
    shipped = {
        "A vendor (htmx, Alpine.js, htmx sse)": [
            size(p) for p in sorted((HERE / "spike/static/spike/vendor").glob("*.js"))
        ],
        "B dist (built JS + CSS)": [
            size(p)
            for p in sorted((HERE / "svelte/dist/assets").glob("*"))
            if p.is_file()
        ],
    }
    shipped = {
        k: {
            "bytes": sum(x["bytes"] for x in v),
            "gzip": sum(x["gzip"] for x in v),
            "files": len(v),
        }
        for k, v in shipped.items()
    }
    print(json.dumps({"loc": table, "shipped": shipped}, indent=1))


main()
