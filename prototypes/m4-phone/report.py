"""Write RESULTS.md (the #39 issue comment body) from results.json. Run after shoot.js."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRANCH = "spike-39-phone-prototypes"

NAMES = {
    "dashboard-a": "Dashboard A: one column, sticky current-heat card",
    "dashboard-b": "Dashboard B: tabs (Now, Rounds, Timer), racing switch in the app bar",
    "dashboard-c": "Dashboard C: docked heat-control sheet over the round list",
    "checkin-a": "Check-in A: card list, pass switch on each card",
    "checkin-b": "Check-in B: scan first, large search and camera button",
    "checkin-c": "Check-in C: grouped by den, sticky group headers",
}
SHEETS = {
    "round": "round actions",
    "newround": "new-round stepper",
    "heat": "expanded heat sheet",
    "racer": "racer details",
    "found": "found racer",
}

COMPARISON = """\
**Dashboard.** A keeps the current heat, the racing switch, the heat buttons, and the timer and replay status on screen at all times, and anything else scrolls under it. The cost is height: the sticky card plus app bar and nav take {a_chrome}px of 844, which leaves {a_content}px for last-heat results and the round cards. B gives the scrolling content the most room ({b_content}px) and puts the racing switch in the app bar, where it stays reachable from every tab. But the app bar is full at 390px, and the round list and timer detail are one tap away behind a tab, so the current heat and the round cards are never on screen together. C shows the round list with the heat schedule expanded, the current heat marked, and a compact control dock ({c_dock}px) with car numbers, the racing switch, Re-run, and Skip. Racer names, Previous, manual results, the status pills, and the last results are one tap away in the expanded sheet. Its dock sits above the bottom nav, so the bottom of the screen has two stacked control areas.

**Check-in.** A shows every racer with a den filter and a running count. Each pass is one tap on the card's switch, and {ca_rows} racer rows fit at the top of the list under a {ca_bar}px filter bar. B leads with the search field and camera button for scanner or tag workflows. Only {cb_rows} rows of "Not passed yet" are visible without scrolling, and the main action (Pass inspection) happens in a sheet after a scan or tap, which adds a step for a manual pass but gives a large confirm target. C fits the most rows ({cc_rows}), because the filter bar is just the search field, and the sticky den header carries each den's own count ("10 of 13 passed"). It drops the den filter: a den is reached by scrolling, not by a tap.
"""


def cell(entry, state):
    s = entry[state]
    v = s["axe"]["violations"]
    t = s["targets"]
    small = t["smallest"]
    viol = "0 violations" if not v else ", ".join(f'{x["id"]} ({x["impact"]}, {x["nodes"]})' for x in v)
    return (
        f'<img src="{s["file"]}" width="195" alt="{entry["layout"]} {entry["scheme"]} {state}"><br>'
        f"axe: {viol}<br>"
        f'smallest target: {small["w"]:g}×{small["h"]:g} px ({small["name"]})<br>'
        f'under 44 px: {len(t["under44"])} of {t["count"]}'
    )


def main():
    results = json.loads((HERE / "results.json").read_text())
    by = {(e["layout"], e["scheme"]): e for e in results}
    layouts = list(NAMES)
    axe_version = results[0]["default"]["axe"]["version"]
    total_viol = sum(len(e[s]["axe"]["violations"]) for e in results for s in ("default", "sheetState"))
    min_dim = min(min(e[s]["targets"]["smallest"]["w"], e[s]["targets"]["smallest"]["h"])
                  for e in results for s in ("default", "sheetState"))
    under44 = sum(len(e[s]["targets"]["under44"]) for e in results for s in ("default", "sheetState"))
    incomplete = sorted({i["id"] for e in results for s in ("default", "sheetState") for i in e[s]["axe"]["incomplete"]})
    errors = sum(len(e["errors"]) for e in results)
    focus_ok = all(e["focusInSheet"] for e in results)
    m = {k: by[(k, "light")]["layoutMetric"] for k in layouts}

    out = []
    out.append("## Phone prototypes: race dashboard and check-in\n")
    out.append(
        f"Static prototypes for the \"Prototype and decision\" section, on branch `{BRANCH}` in "
        "`prototypes/m4-phone/` (not for merging, and nothing under `server/ui/`). Pages are Django templates "
        "rendered by the standalone template engine, with htmx 2.0.4, Alpine.js 3.14.8, and Tailwind 4.1.11 from "
        "the standalone CLI. The data is fixtures only: a 4-lane track, 40 racers in 3 dens (Tigers 14, Wolves 13, "
        "Bears 13), Wolves round 1 racing at heat 6 of 11, and heat 6 running with lane 3 empty. The dashboard shows "
        "the RaceCoordinator bottom nav (Dashboard, Check-In, Standings, More), and check-in shows the RaceCrew nav "
        "(Check-In, Photos, On Deck, More).\n"
    )
    out.append("The layout choice is the owner's. This comment only records what was built and measured.\n")
    out.append("### Method\n")
    out.append(
        "- Playwright Chromium, viewport 390×844 CSS px, device scale 2 (PNG 780×1688), touch on, "
        "`prefers-reduced-motion: reduce` (so sheets are captured fully open), `prefers-color-scheme` light and dark.\n"
        "- The sheet state is opened by tapping the real control (for example, the round's ⋮ button, or a racer row), "
        "then measured and scanned in that state.\n"
        f"- **axe-core {axe_version}**, tags wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa, and best-practice, run on "
        "the whole document in each state.\n"
        "- **Tap targets**: every link, button, input, select, switch, and tab that is rendered and not inside "
        "`inert` content (the page behind an open sheet is `inert`). A radio inside a `<label>` is measured by its label. "
        "The visually hidden skip link is excluded. The smallest is by shorter side. The spec's floor is 44×44.\n"
        "- Polled sections use `hx-trigger=\"every Ns [...]\"` with the PHP intervals (2 s dashboard, 10 s check-in). "
        "Polling pauses while a sheet is open or an input has focus, and the prototype server answers each poll "
        "with 204, the spec's \"nothing changed\" reply.\n"
    )
    out.append("### Summary\n")
    out.append(
        f"- axe violations: **{total_viol}** across all 24 states.\n"
        f"- Smallest tap target in any state: **{min_dim:g} px** on its shorter side. Targets under 44 px: **{under44}**.\n"
        f"- axe \"needs review\" items: {', '.join(incomplete) if incomplete else 'none'}. `skip-link` is reported in each "
        "sheet state, because the skip link's target (`#main`) is inside the inert page while a sheet is open. That is expected.\n"
        f"- Console or page errors: {errors}. Focus moved into the open sheet in {'every' if focus_ok else 'not every'} sheet state.\n"
        "- Lane colors keep their hue in both themes. Badge contrast: lane 1 white on #dc2626 4.83:1, lane 2 white on "
        "#2563eb 5.17:1, lane 3 white on #15803d 5.02:1, lane 4 black on #facc15 13.71:1.\n"
    )
    out.append("### Screenshots\n")
    out.append("| Layout | Light, default | Light, sheet open | Dark, default | Dark, sheet open |")
    out.append("|---|---|---|---|---|")
    for k in layouts:
        light, dark = by[(k, "light")], by[(k, "dark")]
        label = f"**{NAMES[k]}**<br>sheet: {SHEETS[light['sheet']]}"
        out.append(f"| {label} | {cell(light, 'default')} | {cell(light, 'sheetState')} | "
                   f"{cell(dark, 'default')} | {cell(dark, 'sheetState')} |")
    out.append("")
    out.append("### Space at 390×844 (light, scrolled to top)\n")
    out.append("| Layout | Fixed or sticky chrome | Left for scrolling content | Racer rows fully visible |")
    out.append("|---|---|---|---|")
    for k in layouts:
        rows = m[k]["racerRowsVisible"] if k.startswith("checkin") else "n/a"
        out.append(f"| {NAMES[k].split(':')[0]} | {m[k]['chromePx']} px | {m[k]['contentPx']} px | {rows} |")
    out.append(
        "\nChrome is the app bar, the bottom nav, and the layout's own sticky or docked part. "
        "In check-in C the 40 px den header also sticks once the list scrolls.\n"
    )
    out.append("### Trade-offs\n")
    out.append(COMPARISON.format(
        a_chrome=m["dashboard-a"]["chromePx"], a_content=m["dashboard-a"]["contentPx"],
        b_content=m["dashboard-b"]["contentPx"],
        c_dock=m["dashboard-c"]["chromePx"] - 56 - 65,
        ca_rows=m["checkin-a"]["racerRowsVisible"], ca_bar=m["checkin-a"]["chromePx"] - 56 - 65,
        cb_rows=m["checkin-b"]["racerRowsVisible"], cc_rows=m["checkin-c"]["racerRowsVisible"],
    ))
    out.append("### Limits of these numbers\n")
    out.append(
        "- axe checks what it can compute. It does not judge focus order, screen reader wording, or the contrast "
        "of a switch thumb against its track. The dark theme's green \"on\" track with a white thumb is a low-contrast "
        "pair, and the state is also shown by the thumb's position.\n"
        "- The search fields, the den filter, Previous, Re-run, Skip, and Save are visual only. Switches, tabs, sheets, "
        "the stepper, and the radio cards work.\n"
        "- Screenshots use Chromium only. iOS Safari (safe-area insets, `dvh`, focus zoom) was not tested.\n"
        "- Rebuild: `prototypes/m4-phone/build.sh --shoot` (needs uv, "
        "Node, and Playwright with Chromium).\n"
    )
    (HERE / "RESULTS.md").write_text("\n".join(out))
    print("wrote RESULTS.md")


if __name__ == "__main__":
    main()
