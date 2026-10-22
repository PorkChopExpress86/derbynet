# ADR-03. Use Django 5.2 LTS on Python 3.11 or later

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

The server must run on a Raspberry Pi at a race venue, in Docker, and in Debian packages. Each target ships its own Python version, so the Django release must support all of them from one dependency lock.

## Decision

Target Django 5.2 LTS. Support Python 3.11 as the minimum.

## Consequences

CI tests Python 3.11 and Python 3.14. The Docker image runs Python 3.14 from Ubuntu 26.04 LTS (`ubuntu:26.04`). The Debian and Pi packages run Python 3.11 on bookworm. Django 5.2.18 lists Python 3.10 to 3.14 as supported, so one dependency lock covers both. Move to the next Django LTS (6.2) when the Pi packages can drop bookworm.

## Evidence

Raspberry Pi OS based on Debian bookworm ships Python 3.11. Django 6.0 requires Python 3.12. Django 5.2 LTS receives security fixes until April 2028. These are external facts, not facts from this repository.
