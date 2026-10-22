# ADR-10. Keep the kiosk TV pages as they look today

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

ADR-09 redesigns the manager pages for phones. The kiosk pages in `website/kiosks/` serve a different audience on a different kind of screen.

## Decision

Port the 15 pages in `website/kiosks/` to Django templates with the same layout, CSS, and JavaScript. They are outside the mobile redesign.

## Consequences

[#52](https://github.com/PorkChopExpress86/derbynet/issues/52) checks each page against a PHP screenshot at 1920x1080.

## Evidence

Kiosks are large screens. `extras/scripts/kiosk.sh` opens them in fullscreen Chromium on Raspberry Pi units.
