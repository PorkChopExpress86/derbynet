# ADR-08. Keep polling for v1, and add push later

## Status

Accepted on 2026-10-22, when the owner approved merging the ADR pull request. The owner answered the open questions on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

Clients poll constantly. The timer heartbeat runs every 500 ms (`timer/src/org/jeffpiazza/derby/HttpTask.java`), replay every 250 ms (`website/replay.php:87`), the coordinator every 2 s (`website/js/coordinator-poll.js:751`), and each kiosk every 5 s (`website/js/kiosk-poller.js:45`).

## Decision

Keep every polling endpoint and interval. Server push through SSE or Channels is optional and comes after parity.

## Consequences

Plain WSGI with gunicorn is enough. There is no Redis dependency. Poll views must stay cheap. [#56](https://github.com/PorkChopExpress86/derbynet/issues/56) covers push. The kiosk framework spike ([#71](https://github.com/PorkChopExpress86/derbynet/issues/71), owner decision D10) may find that synced kiosk animations need push. If it does, push moves ahead of parity, and a new ADR supersedes this one.

## Evidence

Every display and timer client polls today. The only push channel is `extras/websocket/ws-server.js`, which is optional, and no installer starts it.
