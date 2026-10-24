# ADR-15. Push kiosk updates for sub-second scene switches

## Status

Accepted on 2026-10-24, when the owner answered Q1 in [issue #56](https://github.com/PorkChopExpress86/derbynet/issues/56). It supersedes [ADR-08](0008-keep-polling-for-v1-and-add-push-later.md) for the kiosk pages. ADR-08 still holds for every other client.

## Context

Kiosks poll every 5 s (`website/js/kiosk-poller.js:45`), so a scene change, such as switching from Now Racing to Standings at intermission, reaches each TV at a different moment within those 5 s. The owner wants every kiosk to switch together in under a second. The rebuilt kiosk pages with synced animations ([#52](https://github.com/PorkChopExpress86/derbynet/issues/52), owner decision D10) need the same.

## Decision

The server pushes scene, now-racing, and standings changes to the kiosk pages, and every connected kiosk receives each change within 1 s. Push is part of M5 and is no longer an optional post-parity item. Every polling endpoint stays, unchanged. Kiosks fall back to polling when the push connection drops, and `derby-timer.jar`, the browser timer, and the Pi scripts keep polling ([ADR-02](0002-keep-the-legacy-wire-contract-permanently.md)). [#56](https://github.com/PorkChopExpress86/derbynet/issues/56) and the kiosk framework spike ([#71](https://github.com/PorkChopExpress86/derbynet/issues/71)) choose between SSE and Channels by measuring them.

## Consequences

The server needs a long-lived connection per kiosk, so the deployment in [#58](https://github.com/PorkChopExpress86/derbynet/issues/58) must serve streaming responses. The mechanism #56 picks decides whether that needs an ASGI worker or Redis. Each service that changes a scene, the current heat, or standings has to publish an event. A kiosk that reconnects has to read the current state first, so it can't miss a change. The old `extras/websocket/ws-server.js` relay is dropped at cutover (#56 Q2).

## Evidence

Every display client polls today, and nothing pushes. The owner chose sub-second push over 5 s polling on 2026-10-24.
