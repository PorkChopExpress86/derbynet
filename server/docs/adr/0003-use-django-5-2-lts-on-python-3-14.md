# ADR-03. Use Django 5.2 LTS on Python 3.14

## Status

Accepted on 2026-10-22, when the owner approved merging the ADR pull request. The owner answered the open questions on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

The server ships only as a Docker image (ADR-14), so the Python in that image is the only runtime the Django release must support from one dependency lock.

## Decision

Target Django 5.2 LTS. Python 3.14 in the Docker image is the only supported runtime. There is no Python 3.11 floor.

## Consequences

CI tests Python 3.14 only. The Docker image runs Python 3.14 from Ubuntu 26.04 LTS (`ubuntu:26.04`). One dependency lock covers the image and CI. No platform holds the program on Django 5.2 any more, so a move to the next Django LTS is a version change for a later decision, not a packaging limit.

## Evidence

The 3.11 floor existed only for Debian packages on bookworm, and ADR-14 removes every direct install (owner decision D3). Django 5.2.18 lists Python 3.10 to 3.14 as supported. Django 5.2 LTS receives security fixes until April 2028. These are external facts, not facts from this repository.
