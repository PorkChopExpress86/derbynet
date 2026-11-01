# ADR-10. Rebuild the built-in kiosk pages in a modern front end

## Status

Accepted on 2026-10-22, when the owner approved merging the ADR pull request. The owner answered the open questions on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4). On 2026-11-01 the owner chose the framework that #71 recommended (D62), and said the kiosks are laptops, not Raspberry Pis (D63). The last paragraph of the Decision names it. It fills in the choice the decision left to #71, and the decision itself is unchanged.

## Context

ADR-09 redesigns the manager pages for phones. The kiosk pages in `website/kiosks/` serve a different audience on a different kind of screen.

This ADR replaces the earlier proposal to port the kiosk pages as they look today (owner decision D10).

## Decision

Kiosk pages are built in only. Django runs no custom PHP kiosk pages and loads no pages from `local/kiosks/` or any other folder. The built-in pages are rebuilt, not copied, in a front end that works well with Django and with the replay page and has proper animations. [#71](https://github.com/PorkChopExpress86/derbynet/issues/71) picks the framework. It prototypes the now-racing page and the standings reveal in Django templates with htmx, Alpine.js, and CSS View Transitions, and in Svelte, on a TV-size screen. The owner may build a custom page later in the chosen framework.

The framework #71 chose is Django templates with htmx and Alpine.js, the stack ADR-09 uses for the manager pages, with no Node at build time or at runtime (D62). Motion uses CSS keyframes for elements that enter, and one shared row-move helper (a FLIP helper on the Web Animations API) for rows that change place, such as the standings reveal. View Transitions are used only for whole-board swaps, and only where recordings on a kiosk laptop for #71 show they are cheap (D63); until those recordings exist, no page uses them.

## Consequences

The legacy page strings and the `poll.kiosk` contract stay (ADR-02), so an existing kiosk, a Raspberry Pi running `extras/scripts/kiosk.sh` included, keeps working without a config edit. [#52](https://github.com/PorkChopExpress86/derbynet/issues/52) checks each page for function and animation on a TV, not for pixel parity with PHP. The seeded Wrap-Up scene no longer names the missing `local/kiosks/feedback.kiosk`. Each kiosk view must allow same-origin framing, so the page works inside the replay iframe and keeps the `replay-started` and `replay-ended` pause. The server's frame-options middleware refuses framing today, and #52 adds the exception.

## Evidence

Kiosks are large screens. `extras/scripts/kiosk.sh` opens them in fullscreen Chromium on Raspberry Pi units. The owner's kiosks are laptops running a browser full screen (D63). The seeded Wrap-Up scene in `website/sql/sqlite/scene-tables.inc` names `local/kiosks/feedback.kiosk`, which does not exist.

The spike's results are in the [#71 results comment](https://github.com/PorkChopExpress86/derbynet/issues/71#issuecomment-6538487054). It built now racing, the standings reveal, and the intermission leaders screen in both candidates at 1920x1080, on a 4-vCPU cloud container with no GPU, where Chromium rendered in software. The ranking holds there, and the numbers are not those of a kiosk laptop.

Dropped frames while animating, with the CPU not throttled:

| Page | Templates, keyframes and the row-move helper | Templates with View Transitions | Svelte |
| --- | --- | --- | --- |
| Now racing | 0.5% | 4.0% | 0.7% |
| Standings reveal | 0% | 7.8% | 0.5% |
| Leaders | 2.5% | 10.8% | 0.7% |

With the CPU throttled 4x, the first column dropped 1 to 2% and Svelte 1.5 to 4.5%. Leaving the default whole-page View Transitions snapshot on made the standings reveal drop 56% of its frames.

Both candidates passed the replay iframe check. Svelte would add a Node build stage to the image (`npm ci`, 39 packages), and the templates add none. A push notice brought two TVs to the same animation step 8 to 9 ms apart on average in both candidates. Under polling, now racing can be about 0.25 s apart and a `poll.kiosk` scene change up to 5 s apart, which ADR-15's push addresses.
