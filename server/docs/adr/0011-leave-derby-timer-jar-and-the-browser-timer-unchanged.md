# ADR-11. Leave derby-timer.jar and the browser timer unchanged

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

ADR-02 keeps the wire contract that the timer clients use. Two timer clients exist: `derby-timer.jar`, built from `timer/`, and the browser timer that `timer.php` serves.

## Decision

The server adapts to the timer clients. Neither timer changes in this program.

## Consequences

Django serves `timer.php` from a template with the same JavaScript. The build still generates `profiles.js` from the jar. [#32](https://github.com/PorkChopExpress86/derbynet/issues/32) proves byte parity against the transcripts from [#7](https://github.com/PorkChopExpress86/derbynet/issues/7).

## Evidence

The jar is installed on race-day machines. The browser timer is built from `timer/derbynet-timer/src/js` and `templates/timer.html` by Ant.
