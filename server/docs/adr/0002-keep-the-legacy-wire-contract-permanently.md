# ADR-02. Keep the legacy wire contract permanently

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

Every dynamic operation goes through `website/action.php`. It has 107 handlers (82 actions, 25 queries) and encodes JSON with `JSON_PRETTY_PRINT | JSON_NUMERIC_CHECK` (`website/action.php:107`). Field clients depend on its exact bytes.

The alternative was versioned legacy URLs with a deprecation window. It was rejected because nobody can update the clients in the field.

## Decision

Django serves these URLs with the same request and response shapes, with no deprecation date:

- `/action.php`, with POST `action=` and GET `query=`
- `photo.php/...`, `slide.php/...`, and `image.php/...`
- `kiosk.php?address=...` and `kiosk.php?page=...`
- `post-timer-log.php`
- The `timer-message` XML protocol with its `<action-response>` root
- `index.php`, which `extras/scripts/kiosk.sh` and `extras/scripts/derby-timer.sh` probe before they start
- `timer.php`, the browser timer

The routes work at `/` and under `/derbynet/`.

## Consequences

The legacy routes are exempt from CSRF and never redirect. Errors return HTTP 200 with the same body shape. A compatibility encoder reproduces `JSON_PRETTY_PRINT | JSON_NUMERIC_CHECK`. The new UI may use new endpoints, but both call the same service layer. [#17](https://github.com/PorkChopExpress86/derbynet/issues/17) owns the exact contract.

## Evidence

`timer/src/org/jeffpiazza/derby/ClientSession.java` tries the base URL and then the base plus `derbynet/`. It reads replies with `getElementsByTagName("success")` and `getElementsByTagName("failure")`. `extras/scripts/lib/photo-functions.sh` greps pretty-printed JSON one line at a time (lines 101 and 223). These clients are installed at race venues and cannot be updated with the server.
