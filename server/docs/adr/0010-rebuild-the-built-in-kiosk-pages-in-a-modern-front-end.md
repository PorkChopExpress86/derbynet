# ADR-10. Rebuild the built-in kiosk pages in a modern front end

## Status

Accepted. The owner accepted it on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

ADR-09 redesigns the manager pages for phones. The kiosk pages in `website/kiosks/` serve a different audience on a different kind of screen.

This ADR replaces the earlier proposal to port the kiosk pages as they look today (owner decision D10).

## Decision

Kiosk pages are built in only. Django runs no custom PHP kiosk pages and loads no pages from `local/kiosks/` or any other folder. The built-in pages are rebuilt, not copied, in a front end that works well with Django and with the replay page and has proper animations. [#71](https://github.com/PorkChopExpress86/derbynet/issues/71) picks the framework. It prototypes the now-racing page and the standings reveal in Django templates with htmx, Alpine.js, and CSS View Transitions, and in Svelte, on a TV-size screen. The owner may build a custom page later in the chosen framework.

## Consequences

The legacy page strings and the `poll.kiosk` contract stay (ADR-02), so a Pi kiosk keeps working without a config edit. [#52](https://github.com/PorkChopExpress86/derbynet/issues/52) checks each page for function and animation on a TV, not for pixel parity with PHP. The seeded Wrap-Up scene no longer names the missing `local/kiosks/feedback.kiosk`.

## Evidence

Kiosks are large screens. `extras/scripts/kiosk.sh` opens them in fullscreen Chromium on Raspberry Pi units. The seeded Wrap-Up scene in `website/sql/sqlite/scene-tables.inc` names `local/kiosks/feedback.kiosk`, which does not exist.
