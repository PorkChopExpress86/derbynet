"""#58's phone check (D88): check-in photo capture over the image's HTTPS.

Chromium in phone mode, the browser tests' 390x844 touch size
(`SIZES["phone"]` in ui/tests/browser/conftest.py), opens
`https://<LAN IP>/` after accepting the image's self-signed certificate
(`ignore_https_errors`), logs in as RaceCrew, takes a racer's photo with the
live camera (Chromium's fake camera) and checks the racer in. The live camera
is offered only in a secure context, so the check also asserts the page is
one; over plain HTTP the sheet has only the file input
(`test_a_photo_over_plain_http_uploads_crops_and_shows`).

    uv run python docker/ci/phone_check.py https://192.168.1.20 shot.png

The server needs an event with racers and a photo folder, as
`.github/workflows/image.yml` sets up with testing/'s scripts first. It exits
non-zero when a step fails, and saves the screenshot either way.
"""

import re
import sys

from playwright.sync_api import expect, sync_playwright

# ui/tests/browser/conftest.py's SIZES["phone"].
PHONE = {
    "viewport": {"width": 390, "height": 844},
    "has_touch": True,
    "is_mobile": True,
    "device_scale_factor": 2,
}
PASSWORD = "murphy"  # testing/default.passwords, RaceCrew


def check(base: str, screenshot: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ]
        )
        context = browser.new_context(**PHONE, base_url=base, ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(20_000)
        try:
            page.goto("/login/?all")
            assert page.url.startswith("https://"), page.url
            assert page.evaluate("window.isSecureContext"), "not a secure context"
            page.locator('button[data-role="RaceCrew"]').click()
            password = page.get_by_label("Password")
            password.fill(PASSWORD)
            password.press("Enter")
            page.wait_for_url(re.compile(r"/$"))

            page.goto("/checkin/")
            expect(page.locator("#checkin-list")).to_be_visible()
            racer_photo = re.compile("^Racer photo of ")
            page.get_by_role("button", name=racer_photo).first.click()
            sheet = page.locator('[data-sheet="checkin"]')
            photo = sheet.locator("[data-photo-sheet]")
            live = photo.locator("[data-live]")
            expect(live).to_be_visible()
            live.get_by_role("button", name="Use the live camera").click()
            snap = live.get_by_role("button", name="Capture", exact=True)
            expect(snap).to_be_enabled()
            snap.click()
            expect(photo.locator("[data-preview]")).to_be_visible()
            photo.get_by_role("button", name="Capture and check in").click()
            expect(sheet.locator("[data-crop-sheet]")).to_be_visible()
            expect(sheet.locator("[data-sheet-notice]")).to_contain_text(
                "Racer photo saved"
            )
            print(f"ok: live-camera photo saved over {page.url}")
        finally:
            page.screenshot(path=screenshot, full_page=True)
            browser.close()


if __name__ == "__main__":
    check(sys.argv[1], sys.argv[2])
