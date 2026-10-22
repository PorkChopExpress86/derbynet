# ADR-14. Make Docker and Debian or Pi first-class, and serve Mac and Windows through Docker Desktop

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

DerbyNet ships today as a Docker image, Debian packages, a Mac package, and a Windows build. ADR-06 retires the Windows build's ODBC path.

## Decision

Ship a multi-arch Docker image and Debian packages that run on Raspberry Pi. At cutover, Mac and Windows users run the Docker image.

## Consequences

Every target serves self-signed TLS on 443, because phone cameras need a secure context. [#58](https://github.com/PorkChopExpress86/derbynet/issues/58), [#59](https://github.com/PorkChopExpress86/derbynet/issues/59), and [#60](https://github.com/PorkChopExpress86/derbynet/issues/60) carry this out.

## Evidence

`build.xml` already builds Docker for `linux/amd64` and `linux/arm64`, and `installer/debian/build.xml` builds the `.deb` files. The Mac package needs the PHP that macOS dropped, or MAMP. The Windows build bundles Apache, PHP, and ODBC.
