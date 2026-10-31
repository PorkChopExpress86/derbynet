"""Render the #39 prototype pages to dist/ with Django's template engine, standalone.

No project settings, no database: settings.configure() with one template directory.
"""

import sys
from pathlib import Path

import django
from django.conf import settings

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

settings.configure(
    TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [HERE / "templates"]}],
)
django.setup()

from django.template.loader import render_to_string  # noqa: E402

import fixtures as f  # noqa: E402

COMMON = {
    "timer": f.TIMER,
    "rounds": f.ROUNDS,
    "heat": f.CURRENT_HEAT,
    "next_heat": f.NEXT_HEAT,
    "last_heat": f.LAST_HEAT,
    "last_results": f.LAST_HEAT_RESULTS,
    "heats": f.WOLVES_HEATS,
    "racers": f.RACERS,
    "groups": f.GROUPS,
    "passed": f.PASSED,
    "total": f.TOTAL,
    "not_passed": f.NOT_PASSED,
    "recent": f.RECENT,
    "dens": ["All"] + f.PARTITIONS,
}
DASHBOARD = {"role": "Coordinator", "nav": f.COORDINATOR_NAV, "more": f.COORDINATOR_MORE,
             "active": "dashboard", "title": "Race Dashboard"}
CHECKIN = {"role": "Crew", "nav": f.CREW_NAV, "more": f.CREW_MORE, "active": "checkin", "title": "Check-In"}

PAGES = {
    "dashboard-a": ("dashboard_a.html", {**DASHBOARD, "option": "A"}),
    "dashboard-b": ("dashboard_b.html", {**DASHBOARD, "option": "B", "title": "Dashboard", "appbar_racing": True}),
    "dashboard-c": ("dashboard_c.html", {**DASHBOARD, "option": "C"}),
    "checkin-a": ("checkin_a.html", {**CHECKIN, "option": "A"}),
    "checkin-b": ("checkin_b.html", {**CHECKIN, "option": "B"}),
    "checkin-c": ("checkin_c.html", {**CHECKIN, "option": "C"}),
}


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    links = []
    for slug, (template, ctx) in PAGES.items():
        (out / f"{slug}.html").write_text(render_to_string(template, {**COMMON, **ctx}))
        links.append(f'<li><a href="{slug}.html">{slug}</a></li>')
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        "<title>#39 prototypes</title><ul>" + "".join(links) + "</ul>"
    )
    print(f"rendered {len(PAGES)} pages to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dist")
