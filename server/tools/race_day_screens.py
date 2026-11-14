r"""Screenshots of the race day for the guides in `docs/` (#61, D91).

The script plays the owner's race day (D11) against a server with no event
yet, through its pages as a race coordinator would, and shoots each screen
the guides show into `docs/img/<guide>/`: running-a-race, timer-operation,
photo-upload and balloting. Two classes, Scouts and Outlaws, each race a
first round and a final for their top 4, with an intermission between rounds
(D12). The timer is the legacy timer protocol, played with fixed times
(`car_time`), so a reshoot draws the same results and standings. Its bytes
still differ: the pages show clock times, ages ("2 s ago") and dated file
names. A later ticket that changes one of these screens runs it again (D91).
The racer photos are drawn by `placeholders()`, never taken from
`testing/data/headshots`, so the guides show no real child's face.

Every screen is shot at 390x844, the phone, as the race coordinator's view.
A page also gets a 1280x800 shot, named `-desktop`, when its content lays out
differently at the `lg` breakpoint (1024 px: two columns or a wider grid,
not only the sidebar), or when it is for a computer only: the browser timer,
the replay kiosk, and a kiosk TV (1280x720). Shots are of the window, not
the whole page, saved as 128-colour PNGs.

The script fails when a page logs an error or a failed response, when a
page scrolls sideways, and when the shots and the images the four guides
embed differ (`report()`): a shot no guide uses, an image a guide uses that
was not shot, or an old file in a guide's folder. It refuses a server that
already has an event.

Shoot from the Docker image, so the pages show its paths (`/var/lib/derbynet`)
and version. Serve it on port 80 as `derbynet.local`, a name the script maps
to 127.0.0.1 in its browser, so the links the pages print read like a race
day's. The image's browser timer cannot load its scripts until #312 is
fixed: mount the ones `manage.py build_timer` writes, made readable.
From the repository root:

    docker buildx build -f server/docker/Dockerfile -t derbynet-docs --load .
    (cd server && uv run python manage.py build_timer)
    chmod -R a+rX server/ui/static/timer
    docker run -d --name derbynet-docs -p 127.0.0.1:80:80 \
        -v derbynet-docs:/var/lib/derbynet -e TZ=America/Chicago \
        -e DERBYNET_ALLOWED_HOSTS=derbynet.local \
        -v "$PWD/server/ui/static/timer:/opt/derbynet/server/ui/static/timer:ro" \
        derbynet-docs
    (cd server && uv run python tools/race_day_screens.py http://derbynet.local)
    docker rm -f derbynet-docs && docker volume rm derbynet-docs

Any other server with no event works too, such as `runserver` at
`http://127.0.0.1:8000` after `build_timer`; its pages then show its own
paths. `--text DIR` writes each screen's text and controls beside it, to
check the labels the guides quote. The script uses the full Chromium
(`channel="chromium"`), whose new headless mode treats `derbynet.local` as a
secure page for the camera and Web Serial.
"""

import argparse
import io
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "server"))

from accounts.defaults import DEFAULT_ROLES  # noqa: E402
from ui.tests.browser.helpers import (  # noqa: E402
    dismiss_toasts,
    open_section,
    ready,
)
from ui.tests.browser.helpers import htmx_settled as settled  # noqa: E402

PASSWORDS = {name: password for name, password, _, _ in DEFAULT_ROLES if name}
PHONE = {
    "viewport": {"width": 390, "height": 844},
    "has_touch": True,
    "is_mobile": True,
}
DESKTOP = {"viewport": {"width": 1280, "height": 800}}
CONTROLS = """() => [...document.querySelectorAll(
  'button, a, input, select, textarea, label, h1, h2, h3, summary, [role=switch]')]
 .filter(e => e.offsetParent)
 .map(e => e.tagName + (e.type ? '[' + e.type + ']' : '') + ': '
   + (e.innerText || e.value || e.getAttribute('aria-label') || e.name || '')
     .trim().replace(/\\s+/g, ' ').slice(0, 90))"""


# Scrolls an element's top to just under whatever stays put at the top of
# the window: the app bar, and a sticky filter bar.
SCROLL_TO = """e => {
  const stuck = [...document.querySelectorAll('body *')].filter(x => {
    const p = getComputedStyle(x).position;
    return (p === 'sticky' || p === 'fixed') && x.getBoundingClientRect().top < 120
      && x.offsetHeight < innerHeight / 2 && !x.closest('[data-sheet]');
  });
  const below = Math.max(0, ...stuck.map(x => x.getBoundingClientRect().bottom));
  window.scrollBy(0, e.getBoundingClientRect().top - below - 8);
}"""


class Shooter:
    def __init__(self, out: Path, text: Path | None):
        self.out, self.text = out, text
        self.shots: list[str] = []
        self.errors: list[str] = []

    def watch(self, tab, name):
        """Records each page error, console error and failed response of
        `tab`. A failed load is recorded from its response, which names the
        URL, so the console's copy of it, which names none, is left out. The
        new headless Chromium asks every server for /favicon.ico, which
        DerbyNet has none of."""
        tab.on("pageerror", lambda e: self.errors.append(f"{name} pageerror: {e}"))
        tab.on(
            "console",
            lambda m: (
                m.type == "error"
                and not m.text.startswith("Failed to load resource")
                and self.errors.append(f"{name} console: {m.text} {tab.url}")
            ),
        )
        tab.on(
            "response",
            lambda r: (
                r.status >= 400
                and not r.url.endswith("/favicon.ico")
                and self.errors.append(f"{name} {r.status}: {r.url}")
            ),
        )

    def shot(self, tab, guide: str, name: str, at=None):
        """The window of `tab` as `<out>/<guide>/<name>.png`, with no toast
        over it, scrolled to `at`'s top when given."""
        from PIL import Image

        settled(tab)
        dismiss_toasts(tab)
        if at is not None:
            at.evaluate(SCROLL_TO)
        # Two frames, so the scroll and the last swap are painted.
        tab.evaluate(
            "() => new Promise(r => requestAnimationFrame("
            "() => requestAnimationFrame(r)))"
        )
        width = tab.evaluate("document.documentElement.scrollWidth")
        if width > tab.viewport_size["width"]:
            self.errors.append(f"{guide}/{name} is {width} px wide")
        raw = tab.screenshot(animations="disabled")
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        path = self.out / guide / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.quantize(colors=128, method=Image.Quantize.MEDIANCUT).save(
            path, optimize=True
        )
        self.shots.append(f"{guide}/{name}.png")
        if self.text:
            dump = self.text / guide / f"{name}.txt"
            dump.parent.mkdir(parents=True, exist_ok=True)
            dump.write_text(
                f"URL {tab.url}\n\n{tab.inner_text('body')}\n\n=== CONTROLS\n"
                + "\n".join(tab.evaluate(CONTROLS))
            )
        print("shot", path.relative_to(self.out))


def log_in(tab, role="RaceCoordinator", password=None):
    tab.goto("/login/?all")
    tab.locator(f'button[data-role="{role}"]').click()
    field = tab.get_by_label("Password")
    field.fill(PASSWORDS[role] if password is None else password)
    field.press("Enter")
    tab.wait_for_url(re.compile(r"/$"))


SCOUTS = [
    ("Ava", "Adams"),
    ("Ben", "Brooks"),
    ("Cara", "Cole"),
    ("Dev", "Diaz"),
    ("Eli", "Evans"),
    ("Fay", "Ford"),
    ("Gus", "Grant"),
    ("Hana", "Hill"),
    ("Ivan", "Irwin"),
    ("Jo", "James"),
]
OUTLAWS = [
    ("Kim", "Kane"),
    ("Leo", "Lopez"),
    ("Mia", "Moore"),
    ("Ned", "Nash"),
    ("Opal", "Owens"),
    ("Pat", "Price"),
]
# The cars each of the two families votes for, Best Design taking 2.
VOTES = (("103", "105"), ("103",))
# The racer the check-in crew adds, who is not on the roster.
LATE = ("Quinn", "Quick")


def roster_csv(folder: Path) -> Path:
    lines = ["First Name,Last Name,Class,Car Number"]
    for den, first_car, racers in (("Scouts", 101, SCOUTS), ("Outlaws", 201, OUTLAWS)):
        lines += [
            f"{first},{last},{den},{first_car + i}"
            for i, (first, last) in enumerate(racers)
        ]
    path = folder / "roster.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


TIMES_BASE = 2.8


# Racer photos are drawn here, not taken from testing/data/headshots, so the
# guides show no real child's face (the owner, #61).
COLOURS = ("#2563eb", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0891b2")


def placeholders(folder: Path, names) -> list[Path]:
    """One 600x800 JPEG per racer: a head and shoulders in a light shade on
    a coloured ground, with the racer's initials."""
    from PIL import Image, ImageDraw, ImageFont

    paths = []
    for i, (first, last) in enumerate(names):
        image = Image.new("RGB", (600, 800), COLOURS[i % len(COLOURS)])
        draw = ImageDraw.Draw(image)
        draw.ellipse((180, 170, 420, 410), fill="#e5e7eb")
        draw.ellipse((70, 450, 530, 1000), fill="#e5e7eb")
        font = ImageFont.load_default(size=96)
        draw.text(
            (300, 650), first[0] + last[0], fill="#374151", font=font, anchor="mm"
        )
        path = folder / f"racer-{i + 1:02}.jpg"
        image.save(path, quality=85)
        paths.append(path)
    return paths


def car_time(car: int, lane: int) -> str:
    """A fixed time for each car and lane, so a reshoot draws the same
    results: a lower car number is faster in every lane, so it ranks higher
    in the standings."""
    return f"{TIMES_BASE + car / 400 + lane * 0.011:.3f}"


class Timer:
    """The legacy timer, as `run_heat` in testing/common.sh plays it: the
    Timer role posts timer-message to /action.php."""

    def __init__(self, playwright, base):
        # The timer page shows the client's User-Agent.
        self.api = playwright.request.new_context(
            base_url=base, user_agent="DerbyNet race-day screenshots"
        )
        self.post({"action": "role.login", "name": "Timer", "password": ""})
        self.message("HELLO")
        # Three of the browser timer's own flags (derbynet-timer's flags.js),
        # so the timer settings sheet has some to show.
        self.message(
            "FLAGS",
            **{
                "flag-ignore-place": "bool:false",
                "desc-ignore-place": "Discard any place indications from timer",
                "flag-reset-after-start": "int:11",
                "desc-reset-after-start": "Reset timer <nsec> seconds after heat"
                " start, default 11.  Set 0 for never.",
                "flag-fasttrack-automatic-gate-release": "bool:false",
                "desc-fasttrack-automatic-gate-release": "FastTrack light tree and"
                " automatic gate release installed",
            },
        )
        self.message("IDENTIFIED", lane_count="4", timer="FakeTimer", human="Fake")

    def post(self, form):
        reply = self.api.post("/action.php", form=form)
        assert reply.ok and "success" in reply.text(), reply.text()
        return reply.text()

    def message(self, message, **fields):
        return self.post({"action": "timer-message", "message": message, **fields})

    def heat(self, lineup):
        """STARTED and FINISHED for one heat, from {lane: car}."""
        self.message("STARTED")
        times = {f"lane{lane}": car_time(car, lane) for lane, car in lineup.items()}
        return self.message("FINISHED", **times)


def lineup(api) -> dict[int, int]:
    """The current heat's {lane: car number}, from the data the now-racing
    kiosk reads."""
    reply = api.get("/action.php?query=poll&values=current-heat,racers")
    data = reply.json()
    return {int(r["lane"]): int(r["carnumber"]) for r in data.get("racers", [])}


def walk(s: Shooter, phone, desktop, work: Path, playwright, base, api_base):
    from playwright.sync_api import expect

    # ----- Set-up: the first event, before anyone logs in.
    phone.goto("/")
    if phone.locator('[data-step="event"][data-step-status="unknown"]').count() != 1:
        raise SystemExit(f"{base} already has an event: start a server with none")
    s.shot(phone, "running-a-race", "setup-new-event")
    phone.get_by_label("New event's name").fill("Pack 42 2026")
    phone.get_by_role("button", name="Start a new event").click()
    expect(phone.locator('[data-step="event"]')).to_contain_text("Pack 42 2026")
    phone.goto("/login/")
    s.shot(phone, "running-a-race", "login")
    log_in(phone)
    log_in(desktop)

    # Roles and passwords: the banner asks until each default is changed.
    phone.goto("/settings/roles/")
    s.shot(phone, "running-a-race", "roles")
    for role in ("Photo", "RaceCrew", "RaceCoordinator"):
        card = phone.locator(f'[data-role-card="{role}"]')
        card.get_by_label(f"New password for {role}").fill(NEW_PASSWORDS[role])
        card.get_by_role("button", name="Change password").click()
        settled(phone)
    phone.goto("/setup/")
    s.shot(phone, "running-a-race", "setup-links", at=phone.locator("[data-links]"))
    phone.goto("/settings/")
    track = open_section(phone, "track")
    track.get_by_label("Lanes on the track").fill("4")
    track.get_by_label("Lanes on the track").press("Enter")
    expect(track.locator('select[name="color"]')).to_have_count(4)
    track.scroll_into_view_if_needed()
    s.shot(phone, "running-a-race", "settings-track", at=track)

    # ----- Roster import.
    phone.goto("/import/roster/")
    phone.locator("[data-file]").set_input_files(str(roster_csv(work)))
    expect(phone.locator("[data-step=map]")).to_be_visible()
    s.shot(phone, "running-a-race", "import-map", at=phone.locator("[data-step=map]"))
    phone.get_by_role("button", name="Preview").click()
    settled(phone)
    phone.get_by_role("button", name="Import").click()
    settled(phone)
    expect(phone.locator("[data-imported]")).to_contain_text("Imported 16 racers")
    s.shot(phone, "running-a-race", "import-done", at=phone.locator("[data-imported]"))

    # ----- Racing groups.
    phone.goto("/groups/")
    s.shot(phone, "running-a-race", "groups")
    desktop.goto("/groups/")
    s.shot(desktop, "running-a-race", "groups-desktop")

    # ----- Weigh-in: Scouts record a weight in ounces, 5 oz at most.
    phone.goto("/settings/")
    section = open_section(phone, "weigh-in")
    row = section.locator('[data-weigh-in="Scouts"]')
    row.get_by_text("Recorded weight").click()
    row.locator('select[name="unit"]').select_option("oz")
    row.get_by_label(re.compile("Maximum weight")).fill("5")
    row.get_by_role("button", name="Save").click()
    expect(row.locator("[data-saved]")).to_be_visible()
    s.shot(phone, "running-a-race", "settings-weigh-in", at=section)

    # ----- Awards: a design award the audience votes on.
    phone.goto("/awards/edit/")
    phone.locator('[data-action="new-award"]').click()
    form = phone.locator('[data-award-sheet="new"]')
    expect(form).to_be_visible()
    settled(phone)
    form.get_by_label("Award name").fill("Best Design")
    form.get_by_label("Type").select_option(label="Design General")
    form.get_by_role("button", name="Add award").click()
    settled(phone)
    s.shot(phone, "running-a-race", "awards-editor")
    desktop.goto("/awards/edit/")
    s.shot(desktop, "running-a-race", "awards-editor-desktop")

    # ----- The rounds playlist (D11, D12).
    phone.goto("/playlist/")
    for name, top in (
        ("Scouts, Round 1", None),
        ("Scouts, Round 2", 4),
        ("Outlaws, Round 1", None),
        ("Outlaws, Round 2", 4),
    ):
        phone.get_by_role("button", name=re.compile(f"^Add {name}")).click()
        form = phone.locator("[data-entry-sheet]")
        expect(form).to_be_visible()
        settled(phone)
        if top:
            form.get_by_label("How many racers go to this final?").fill(str(top))
            if name.startswith("Scouts"):
                s.shot(phone, "running-a-race", "playlist-final-sheet")
        form.get_by_role("button", name="Add to playlist").click()
        expect(form).to_be_hidden()
        settled(phone)
    s.shot(phone, "running-a-race", "playlist", at=phone.locator("[data-queue]"))
    desktop.goto("/playlist/")
    s.shot(desktop, "running-a-race", "playlist-desktop")

    # ----- Kiosks: a TV opens /kiosk.php, and the dashboard names it.
    kiosk = desktop.context.browser.new_context(
        viewport={"width": 1280, "height": 720}, base_url=base
    ).new_page()
    s.watch(kiosk, "kiosk")
    kiosk.goto("/kiosk.php?address=gym-tv")
    phone.goto("/kiosks/")
    expect(phone.locator("[data-name]").first).to_be_visible()
    s.shot(phone, "running-a-race", "kiosks")
    desktop.goto("/kiosks/")
    s.shot(desktop, "running-a-race", "kiosks-desktop")

    # ----- Check-in, with the weigh-in and a car photo.
    cars = sorted((REPO / "testing" / "data" / "carphotos").glob("Car-*.jpg"))
    phone.goto("/checkin/")
    expect(phone.locator("#checkin-list")).to_be_visible()
    s.shot(phone, "running-a-race", "checkin")
    desktop.goto("/checkin/")
    s.shot(desktop, "running-a-race", "checkin-desktop")
    racer_card = phone.locator("[data-racer]").filter(has_text="Ava Adams")
    racer_card.get_by_label(re.compile("^Weight")).fill("4.8")
    racer_card.get_by_role("button", name="Save weight").click()
    expect(racer_card.locator("[data-weight]")).to_have_text("4.8 oz")
    search(phone, "Ava")
    s.shot(phone, "running-a-race", "checkin-weighed")

    # The car photo, then the pass, in one sheet.
    tap(phone, racer_card.get_by_role("button", name="Car photo of Ava Adams"))
    sheet = phone.locator('[data-sheet="checkin"]')
    ready(phone, sheet)
    s.shot(phone, "photo-upload", "checkin-photo-sheet")
    sheet.locator("[data-pick]").set_input_files(str(cars[0]))
    expect(sheet.locator("[data-preview]")).to_be_visible(timeout=20000)
    s.shot(phone, "photo-upload", "checkin-photo-preview")
    sheet.get_by_role("button", name="Capture and check in").click()
    crop = sheet.locator("[data-crop-sheet]")
    expect(crop).to_be_visible()
    expect(crop.get_by_role("button", name="Crop", exact=True)).to_be_enabled()
    s.shot(phone, "photo-upload", "checkin-crop")
    crop.get_by_role("button", name="Crop", exact=True).click()
    expect(sheet).to_be_hidden()
    expect(racer_card).to_have_attribute("data-passed", "1")
    s.shot(phone, "running-a-race", "checkin-passed")

    # The racer's own sheet: tap the name.
    tap(phone, racer_card.locator('[data-action="edit"]'))
    ready(phone, sheet)
    s.shot(phone, "running-a-race", "checkin-racer-sheet")
    close_sheet(phone)

    # A late racer, added at the desk.
    search(phone, "")
    tap(phone, phone.get_by_role("button", name="New racer"))
    form = sheet.locator('[data-racer-sheet="new"]')
    ready(phone, form)
    form.get_by_label("Class").select_option(label="Outlaws")
    settled(phone)
    form.get_by_label("First name").fill("Quinn")
    form.get_by_label("Last name").fill("Quick")
    form.get_by_label("Car name").fill("Blue Streak")
    s.shot(phone, "running-a-race", "checkin-new-racer")
    form.get_by_role("button", name="Add racer").click()
    expect(sheet.locator("[data-notice]")).to_contain_text("Added 207 Quinn Quick")
    close_sheet(phone)

    # Every other racer weighs 4.9 oz and passes; a car photo for each.
    others = [*SCOUTS[1:], *OUTLAWS, LATE]
    for car, (first, last) in zip(cars[1 : 1 + len(others)], others, strict=True):
        name = f"{first} {last}"
        search(phone, name)
        one = phone.locator("[data-racer]").filter(has_text=name)
        weight = one.get_by_label(re.compile("^Weight"))
        if weight.count():
            weight.fill("4.9")
            one.get_by_role("button", name="Save weight").click()
            expect(one.locator("[data-weight]")).to_have_text("4.9 oz")
        tap(phone, one.get_by_role("button", name=f"Car photo of {name}"))
        ready(phone, sheet)
        sheet.locator("[data-pick]").set_input_files(str(car))
        expect(sheet.locator("[data-preview]")).to_be_visible(timeout=20000)
        sheet.get_by_role("button", name="Capture and check in").click()
        expect(crop).to_be_visible()
        expect(crop.get_by_role("button", name="Crop", exact=True)).to_be_enabled()
        crop.get_by_role("button", name="Crop", exact=True).click()
        expect(sheet).to_be_hidden()
        expect(one).to_have_attribute("data-passed", "1")
    search(phone, "")
    phone.reload()
    s.shot(phone, "running-a-race", "checkin-all-passed")
    tap(phone, phone.get_by_role("button", name="Bulk", exact=True))
    ready(phone, phone.locator('[data-sheet="checkin"] [data-bulk]'))
    s.shot(phone, "running-a-race", "checkin-bulk")
    close_sheet(phone)
    tap(phone, phone.get_by_role("button", name="Phone QR", exact=True))
    qr = ready(phone, phone.locator('[data-sheet="checkin"] [data-qr]'))
    expect(qr.locator("svg")).to_be_visible()
    s.shot(phone, "running-a-race", "checkin-phone-qr")
    close_sheet(phone)

    # A racer's voting code (D86).
    search(phone, "Ben Brooks")
    tap(phone, phone.get_by_role("button", name="Voting code of Ben Brooks"))
    code = phone.locator("[data-voting-code]")
    expect(code.locator("svg")).to_be_visible()
    vote_urls = [code.locator("[data-voting-url]").inner_text()]
    s.shot(phone, "balloting", "voting-code")
    close_sheet(phone)
    search(phone, "Kim Kane")
    tap(phone, phone.get_by_role("button", name="Voting code of Kim Kane"))
    expect(code.locator("svg")).to_be_visible()
    vote_urls.append(code.locator("[data-voting-url]").inner_text())
    close_sheet(phone)

    # ----- Racer photos: upload a batch, then tap a photo and its racer.
    names = [*SCOUTS, *OUTLAWS, LATE]
    heads = placeholders(work, names)
    phone.goto("/photos/racers/")
    s.shot(phone, "photo-upload", "racer-photos")
    phone.locator("[data-upload-input]").set_input_files(
        [str(h) for h in heads[: len(names)]]
    )
    expect(phone.locator('[data-upload-item][data-state="done"]')).to_have_count(
        len(names), timeout=60000
    )
    phone.reload()
    for i, (_, last) in enumerate(names):
        photo = phone.get_by_role("button", name=re.compile(r"^Assign racer-")).first
        tap(phone, photo)
        choices = phone.locator("[data-racer-choices]")
        ready(phone, choices)
        choices.get_by_label("Find a racer by name or car number").fill(last)
        settled(phone)
        if i == 0:
            s.shot(phone, "photo-upload", "assign-sheet")
        choices.locator("[data-choice]").first.click()
        expect(phone.locator("[data-sheet]:visible")).to_have_count(0)
        settled(phone)
    s.shot(phone, "photo-upload", "racer-photos-assigned")
    desktop.goto("/photos/racers/")
    s.shot(desktop, "photo-upload", "racer-photos-desktop")

    # ----- Kiosks: name the TV, so the scenes reach it.
    phone.goto("/kiosks/")
    tv = phone.locator('#kiosk-board [data-kiosk="gym-tv"]')
    tap(phone, tv.locator('[data-action="name"]'))
    form = ready(phone, phone.locator('[data-sheet="kiosks"] [data-name-sheet]'))
    form.locator('[data-name-chips] button[data-value="Main"]').click()
    expect(form.get_by_label("Name for this kiosk")).to_have_value("Main")
    s.shot(phone, "running-a-race", "kiosk-name-sheet")
    form.locator("[data-submit]").click()
    expect(tv.locator("[data-name]")).to_have_text("Main")

    # ----- The timer, and replay.
    timer = Timer(playwright, api_base)
    phone.goto("/timer/")
    s.shot(phone, "timer-operation", "timer")
    desktop.goto("/timer/")
    s.shot(desktop, "timer-operation", "timer-desktop")
    tap(phone, phone.locator('[data-action="timer-settings"]'))
    ready(phone, phone.locator("#timer-sheet-body > *").first)
    s.shot(phone, "timer-operation", "timer-settings")
    close_sheet(phone)
    browser_timer = desktop.context.new_page()
    s.watch(browser_timer, "browser timer")
    browser_timer.goto("/timer.php")
    expect(browser_timer.get_by_role("button", name="Start Scanning")).to_be_visible()
    s.shot(browser_timer, "timer-operation", "browser-timer-desktop")
    browser_timer.close()
    phone.goto("/timer/test/")
    s.shot(phone, "timer-operation", "timer-test")
    phone.goto("/timer/fake/")
    s.shot(phone, "timer-operation", "fake-timer")
    desktop.goto("/timer/fake/")
    s.shot(desktop, "timer-operation", "fake-timer-desktop")
    desktop.goto("/dashboard/")

    replay = desktop.context.browser.new_context(
        viewport={"width": 1280, "height": 720}, base_url=base
    ).new_page()
    s.watch(replay, "replay")
    log_in(replay, password=NEW_PASSWORDS["RaceCoordinator"])
    replay.goto("/replay/")
    expect(replay.locator("[data-stream-size]")).to_have_text(re.compile(r"^\d+x\d+"))
    s.shot(replay, "running-a-race", "replay-setup-desktop")
    replay.get_by_label("Change to fullscreen").uncheck()
    replay.get_by_role("button", name="Proceed").click()
    expect(replay.locator("#interior")).to_be_visible()

    # ----- Race day: start the playlist from the dashboard.
    timer.message("HEARTBEAT")
    phone.goto("/dashboard/")
    s.shot(phone, "running-a-race", "dashboard-ready")
    # A round's Schedule, as a race with no playlist starts it.
    tap(phone, phone.get_by_role("button", name="Schedule", exact=True).first)
    ready(phone, phone.locator("[data-sheet]:visible"))
    s.shot(phone, "running-a-race", "dashboard-schedule-sheet")
    close_sheet(phone)
    tap(phone, phone.locator('[data-action="replay-settings"]'))
    ready(phone, phone.locator("[data-replay-settings]"))
    s.shot(phone, "running-a-race", "dashboard-replay-settings")
    close_sheet(phone)
    tap(phone, phone.get_by_role("button", name="Start Playlist"))
    settled(phone)
    expect(phone.locator("[data-heat-text]")).to_contain_text("Heat 1")
    s.shot(phone, "running-a-race", "dashboard-racing")
    desktop.goto("/dashboard/")
    s.shot(desktop, "running-a-race", "dashboard-racing-desktop")
    api = playwright.request.new_context(base_url=api_base)
    rounds = ["Scouts, Round 2", "Outlaws, Round 1", "Outlaws, Round 2", None]
    for number, next_round in enumerate(rounds):
        heats = 0
        while current := lineup(api):
            reply = timer.heat(current)
            heats += 1
            if number == 0 and heats == 1:
                expect(phone.locator('[data-lane="1"] [data-result]')).to_have_text(
                    re.compile(r"\d\.\d{3}")
                )
                s.shot(phone, "running-a-race", "dashboard-heat-result")
                tap(phone, phone.locator('[data-action="manual-results"]'))
                ready(phone, phone.locator("[data-results-form]"))
                s.shot(phone, "running-a-race", "dashboard-manual-results")
                close_sheet(phone)
            if "heat-ready" not in reply:
                break
        if next_round is None:
            break
        intermission = phone.locator("[data-intermission]")
        expect(intermission).to_contain_text(f"Up next: {next_round}", timeout=10000)
        # The heat card holds the heat it last showed for 10 s after the
        # current heat moves on (`ui.dashboard.state._hold`, PHP's
        # generate_current_heat_racers()). The heats here come faster than
        # its 2 s poll, so wait for the hold to end.
        expect(phone.locator("[data-hold-note]")).to_have_count(0, timeout=20000)
        if number == 0:
            s.shot(phone, "running-a-race", "dashboard-intermission", at=intermission)
        tap(phone, intermission.get_by_role("button", name="Start next round"))
        expect(phone.locator("[data-intermission]")).to_have_count(0)
        timer.message("HEARTBEAT")
    phone.reload()
    s.shot(phone, "running-a-race", "dashboard-done")

    # ----- Results and standings.
    phone.goto("/results/")
    s.shot(phone, "running-a-race", "results")
    phone.goto("/standings/")
    s.shot(phone, "running-a-race", "standings")
    desktop.goto("/standings/")
    s.shot(desktop, "running-a-race", "standings-desktop")

    # ----- Balloting (D86): open the ballot, and two families vote.
    phone.goto("/judging/")
    tap(phone, phone.locator("[data-open-ballot]"))
    ballot = ready(phone, phone.locator("[data-ballot-sheet]"))
    best = ballot.locator("[data-ballot-award]").filter(has_text="Best Design")
    best.locator("select").select_option("2")
    settled(phone)
    ballot = ready(phone, phone.locator("[data-ballot-sheet]"))
    ballot.get_by_role("switch").first.click()
    settled(phone)
    s.shot(phone, "balloting", "ballot-sheet")
    close_sheet(phone)
    voter = desktop.context.browser.new_context(**PHONE, base_url=base).new_page()
    s.watch(voter, "voter")
    for n, (url, cars_chosen) in enumerate(zip(vote_urls, VOTES, strict=True)):
        voter.goto(url)
        if n == 0:
            s.shot(voter, "balloting", "vote-awards")
        voter.locator("[data-award-card] button").filter(has_text="Best Design").click()
        expect(voter.locator("[data-award-view]")).to_be_visible()
        for car in cars_chosen:
            voter.locator("[data-racer-cards] [data-car]:visible", has_text=car).click()
            toggle = voter.locator("[data-vote-toggle]")
            expect(toggle).to_be_enabled()
            if n == 0 and car == "103":
                s.shot(voter, "balloting", "vote-car")
            toggle.click()
            expect(toggle).to_have_attribute("aria-pressed", "true")
            voter.locator('[data-sheet="vote-racer"] [data-close]').click()
        if n == 0:
            s.shot(voter, "balloting", "vote-chosen")
    voter.context.close()
    phone.goto("/judging/")
    tap(phone, phone.locator("[data-open-ballot]"))
    ballot = ready(phone, phone.locator("[data-ballot-sheet]"))
    tally = ballot.locator("[data-tally]")
    expect(tally).to_be_visible()
    s.shot(phone, "balloting", "ballot-tally", at=tally)
    tally.locator("[data-choose]").first.click()
    settled(phone)
    close_sheet(phone)

    # ----- Judging by taps: an award, then its racer (D95).
    phone.goto("/awards/edit/")
    tap(phone, phone.locator('[data-action="new-award"]'))
    form = ready(phone, phone.locator('[data-award-sheet="new"]'))
    form.get_by_label("Award name").fill("Most Creative")
    form.get_by_label("Type").select_option(label="Design General")
    form.get_by_role("button", name="Add award").click()
    settled(phone)
    phone.goto("/judging/")
    s.shot(phone, "running-a-race", "judging")
    desktop.goto("/judging/")
    s.shot(desktop, "running-a-race", "judging-desktop")
    creative = phone.locator("[data-award-card]").filter(has_text="Most Creative")
    tap(phone, creative.locator("[data-open-award]"))
    eligible = ready(phone, phone.locator('[data-sheet="judging"] [data-eligible]'))
    s.shot(phone, "running-a-race", "judging-award-sheet")
    eligible.locator("li", has_text="Kim Kane").locator("button").click()
    settled(phone)
    close_sheet(phone)

    # ----- Award presentations: the TV shows the awards page.
    phone.goto("/kiosks/")
    tap(phone, tv.locator('[data-action="page"]'))
    picker = ready(phone, phone.locator('[data-sheet="kiosks"] [data-page-sheet]'))
    picker.locator('[data-page-choice="award-presentations"]').click()
    expect(tv.locator("[data-page]")).to_have_text("Award presentations")
    phone.goto("/awards/present/")
    s.shot(phone, "running-a-race", "present")
    rows = phone.locator("[data-presentation-list] > li")
    tap(phone, rows.filter(has_text="Best Design").get_by_role("button").first)
    settled(phone)
    tap(phone, phone.get_by_role("button", name="Reveal"))
    settled(phone)
    s.shot(phone, "running-a-race", "present-revealed")
    desktop.goto("/awards/present/")
    s.shot(desktop, "running-a-race", "present-desktop")
    expect(kiosk.get_by_text("Cara Cole")).to_be_visible(timeout=10000)
    s.shot(kiosk, "running-a-race", "kiosk-award-tv")

    # ----- Export.
    phone.goto("/export/")
    s.shot(phone, "running-a-race", "export")
    phone.goto("/setup/")
    snapshots = phone.locator('[data-section="snapshots"]')
    s.shot(phone, "running-a-race", "setup-snapshots", at=snapshots)
    phone.goto("/setup/events/")
    s.shot(phone, "running-a-race", "events")
    phone.goto("/history/")
    s.shot(phone, "running-a-race", "retrospective")


# True once check-in's list shows only the racers `text` finds, or every
# racer for an empty `text`: the count reads "N shown" only while filtered.
FILTERED = """text => {
  const cards = [...document.querySelectorAll('#checkin-list [data-racer]')];
  const count = document.querySelector('#checkin-list [data-count]');
  if (!count) return false;
  if (!text) return !count.textContent.includes('shown');
  return count.textContent.includes('shown') && cards.length > 0
    && cards.every(c => c.innerText.toLowerCase().includes(text.toLowerCase()));
}"""


def search(tab, text):
    """Type `text` into check-in's search field, and wait for the list."""
    tab.get_by_label("Find a racer by name or car number").first.fill(text)
    tab.wait_for_function(FILTERED, arg=text)
    settled(tab)


def tap(tab, control):
    settled(tab)
    dismiss_toasts(tab)
    control.click()


def close_sheet(tab):
    from playwright.sync_api import expect

    tab.keyboard.press("Escape")
    expect(tab.locator("[data-sheet]:visible")).to_have_count(0)


# The passwords the race day changes the defaults to.
NEW_PASSWORDS = {
    "Photo": "say-cheese",
    "RaceCrew": "pit-crew",
    "RaceCoordinator": "grand-prix",
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base", help="the server, such as http://derbynet.local")
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "img")
    parser.add_argument("--text", type=Path)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    s = Shooter(args.out, args.text)
    flags = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
    parts = urlsplit(args.base)
    api_base = args.base
    named = parts.hostname not in ("localhost", "127.0.0.1")
    if named and not parts.hostname[0].isdigit():
        # A name such as derbynet.local, which the pages then show in the
        # links they print (a voting code, the replay kiosk), for the server
        # on this machine. The API requests go to 127.0.0.1 itself.
        flags.append(f"--host-resolver-rules=MAP {parts.hostname} 127.0.0.1")
        # The camera and Web Serial need a secure page, as on localhost.
        flags.append(f"--unsafely-treat-insecure-origin-as-secure={args.base}")
        netloc = parts.netloc.replace(parts.hostname, "127.0.0.1")
        api_base = parts._replace(netloc=netloc).geturl()
    wait_for_server(api_base)
    with sync_playwright() as p, tempfile.TemporaryDirectory() as work:
        # The full Chromium's new headless mode: the headless shell ignores
        # --unsafely-treat-insecure-origin-as-secure.
        browser = p.chromium.launch(
            channel="chromium", headless=not args.headed, args=flags
        )
        tabs = []
        for name, size in (("phone", PHONE), ("desktop", DESKTOP)):
            context = browser.new_context(**size, base_url=args.base)
            context.set_default_timeout(15000)
            tab = context.new_page()
            s.watch(tab, name)
            tabs.append(tab)
        try:
            walk(s, *tabs, Path(work), p, args.base, api_base)
        finally:
            browser.close()
    return report(s.errors, s.shots, args.out)


def wait_for_server(base: str, seconds: int = 90) -> None:
    """Waits until the server answers its Set-Up page, as a container just
    started takes a few seconds to."""
    from urllib.error import URLError
    from urllib.request import urlopen

    for _ in range(seconds):
        try:
            with urlopen(f"{base.rstrip('/')}/setup/", timeout=5):
                return
        except URLError, OSError:
            time.sleep(1)
    raise SystemExit(f"{base} did not answer in {seconds} s")


def report(errors: list[str], shots: list[str], out: Path) -> int:
    """Writes the run's errors, and what `unmatched()` finds, to stderr, and
    returns the exit status: 1 when there is any. This end-of-run check is
    what fails a run whose walk skipped a shot a guide embeds."""
    found = errors + unmatched(shots, out)
    sys.stderr.write("".join(f"{e}\n" for e in found))
    return 1 if found else 0


GUIDES = ("running-a-race", "timer-operation", "photo-upload", "balloting")
EMBED = re.compile(r"!\[[^\]]*\]\(img/([\w-]+/[\w.-]+)\)")


def embedded() -> set[str]:
    """The images the four guides embed, as `<guide>/<name>.png`."""
    found = set()
    for guide in GUIDES:
        found |= set(EMBED.findall((REPO / "docs" / f"{guide}.md").read_text()))
    return found


def unmatched(shots: list[str], out: Path) -> list[str]:
    """Each shot no guide embeds, each embedded image not shot, and each
    file in a guide's folder under `out` that this run did not write."""
    taken, used = set(shots), embedded()
    files = {
        f"{guide}/{path.name}"
        for guide in GUIDES
        for path in (out / guide).glob("*")
        if (out / guide).is_dir()
    }
    return (
        [f"shot, but no guide embeds it: {name}" for name in sorted(taken - used)]
        + [
            f"a guide embeds it, but it was not shot: {name}"
            for name in sorted(used - taken)
        ]
        + [f"left over from an earlier run: {name}" for name in sorted(files - taken)]
    )


if __name__ == "__main__":
    sys.exit(main())
