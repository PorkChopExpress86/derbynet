# ADR-10. Rebuild the built-in kiosk pages in a modern front end

## Status

Accepted on 2026-10-22, when the owner approved merging the ADR pull request. The owner answered the open questions on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4). On 2026-11-01 the owner chose the framework that #71 recommended (D62), and the Framework section below names it. This fills in the choice the decision left to #71 and does not change the decision.

## Context

ADR-09 redesigns the manager pages for phones. The kiosk pages in `website/kiosks/` serve a different audience on a different kind of screen.

This ADR replaces the earlier proposal to port the kiosk pages as they look today (owner decision D10).

## Decision

Kiosk pages are built in only. Django runs no custom PHP kiosk pages and loads no pages from `local/kiosks/` or any other folder. The built-in pages are rebuilt, not copied, in a front end that works well with Django and with the replay page and has proper animations. [#71](https://github.com/PorkChopExpress86/derbynet/issues/71) picks the framework. It prototypes the now-racing page and the standings reveal in Django templates with htmx, Alpine.js, and CSS View Transitions, and in Svelte, on a TV-size screen. The owner may build a custom page later in the chosen framework.

## Framework

The kiosk pages are Django templates with htmx and Alpine.js, the stack ADR-09 uses for the manager pages, with no Node at build time or at runtime (D62).

- Motion uses CSS keyframes for elements that enter, and one shared helper that moves rows with the Web Animations API (a FLIP helper) for rows that change place, such as the standings reveal.
- View Transitions are used only on a page where the Pi 4 recordings show they drop few frames. Pages that use them set `view-transition-name: none` on the root, because the default whole-page snapshot made the standings reveal drop more than half its frames in the spike.
- Alpine shows and hides animated elements with a bound `hidden` attribute rather than `x-show`, which shows an element one frame late and so hides its entry motion.
- Every kiosk view allows same-origin framing, so the page works inside the replay iframe and keeps the `replay-started` and `replay-ended` pause.
- An owner-built custom page is one more template in the same stack.

## Consequences

The legacy page strings and the `poll.kiosk` contract stay (ADR-02), so a Pi kiosk keeps working without a config edit. [#52](https://github.com/PorkChopExpress86/derbynet/issues/52) checks each page for function and animation on a TV, not for pixel parity with PHP. The seeded Wrap-Up scene no longer names the missing `local/kiosks/feedback.kiosk`.

## Evidence

Kiosks are large screens. `extras/scripts/kiosk.sh` opens them in fullscreen Chromium on Raspberry Pi units. The seeded Wrap-Up scene in `website/sql/sqlite/scene-tables.inc` names `local/kiosks/feedback.kiosk`, which does not exist.

The spike in [#71](https://github.com/PorkChopExpress86/derbynet/issues/71) built now racing, the standings reveal, and the intermission leaders screen in both candidates at 1920x1080, on the branch `spike-71-kiosk-frameworks`. It ran on a 4-vCPU VM with software rendering, so the ranking holds and the numbers are not those of a Pi.

| Page | Templates, keyframes and the row helper | Templates with View Transitions | Svelte |
| --- | --- | --- | --- |
| Now racing | 0.5% dropped frames | 4.0% | 0.7% |
| Standings reveal | 0% | 7.8% | 0.5% |
| Leaders | 2.5% | 10.8% | 0.7% |

Both candidates passed the replay iframe check. Svelte added a Node build stage to the image (`npm ci`, 39 packages), and the templates added none. Under polling, two TVs showed the same step about 0.25 s apart on now racing, and under a push notice 8 ms apart on average, the same for both candidates. The Raspberry Pi 4 recordings are still to come.
