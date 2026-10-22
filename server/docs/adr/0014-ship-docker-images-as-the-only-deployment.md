# ADR-14. Ship Docker images as the only deployment

## Status

Accepted. The owner accepted it on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

DerbyNet ships today as a Docker image, Debian packages, a Mac package, and a Windows build. ADR-06 retires the Windows build's ODBC path.

This ADR replaces the earlier proposal to ship Debian and Pi packages next to the Docker image (owner decision D3).

## Decision

Ship the server only as a multi-arch Docker image. There is no direct install of any kind, so no Debian package, Mac package, or Windows build. A local laptop and a hosted server run the same images. Mac and Windows users run Docker Desktop. When Postgres is chosen, it runs as a container too (ADR-05).

## Consequences

The image serves self-signed TLS on 443, because phone cameras need a secure context. [#58](https://github.com/PorkChopExpress86/derbynet/issues/58) carries this out. [#60](https://github.com/PorkChopExpress86/derbynet/issues/60) is closed as not planned. Docker Desktop on a Mac cannot pass a USB serial device into a container, so laptops connect timers through the browser timer (`timer.php`, Web Serial in Chrome). A Linux or Pi host could run `derby-timer.jar` in a container with `--device`. Container images for the Pi timer and photo stand are in the backlog ([#59](https://github.com/PorkChopExpress86/derbynet/issues/59)), by the default the owner applied in D4. Until then, existing Pi installs keep working through the legacy wire contract. A kiosk Pi is a browser pointed at the server, with nothing to deploy beyond the browser. That last point is a default the owner can change.

## Evidence

`build.xml` already builds Docker for `linux/amd64` and `linux/arm64`. The Mac package needs the PHP that macOS dropped, or MAMP. The Windows build bundles Apache, PHP, and ODBC.
