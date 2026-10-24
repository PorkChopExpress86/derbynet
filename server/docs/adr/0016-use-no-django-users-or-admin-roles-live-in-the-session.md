# ADR-16. Use no Django users or admin; roles live in the session

## Status

Accepted on 2026-10-24 (owner decision D22, [#15](https://github.com/PorkChopExpress86/derbynet/issues/15)). It refines [ADR-07](0007-keep-shared-password-roles-stored-hashed-in-the-database.md), which keeps the shared-password roles and does not say where a logged-in role lives.

## Context

DerbyNet has no per-person accounts. A visitor logs in as one of a few roles, each with one shared password and a permission bitmask ([ADR-07](0007-keep-shared-password-roles-stored-hashed-in-the-database.md)). Django's user model, `django.contrib.auth`, models people, groups, and per-user permissions, and its admin edits those rows. Neither matches the role model, and two login systems would disagree about who is logged in. The first migration of `django.contrib.auth` also fixes a `User` table that the project can't remove later without a data migration.

## Decision

The server installs no `django.contrib.auth`, `django.contrib.admin`, or `django.contrib.messages`. It installs no `AuthenticationMiddleware`, `MessageMiddleware`, or auth context processor, sets no `AUTH_PASSWORD_VALIDATORS`, and leaves `AUTH_USER_MODEL` unset. There is no admin URL. A login stores the role and its permission bits in the session (`django.contrib.sessions`), and the permission checks of [#17](https://github.com/PorkChopExpress86/derbynet/issues/17) read them from there. Role passwords are hashed and checked with `django.contrib.auth.hashers`, which is a plain module and needs no installed app. The `core` app serves a `health/` page that renders a URL, a static path, and a CSRF token and writes nothing, so tests and health probes have a real page to request.

## Consequences

The database holds only the `contenttypes` and `sessions` tables from Django, plus the project's own models, and no `auth_*` tables. Managers edit roles and passwords on DerbyNet's own pages, built under [#16](https://github.com/PorkChopExpress86/derbynet/issues/16). A test keeps `make_password` and `check_password` working with `django.contrib.auth` absent. Adding the auth app later would need a new ADR and a first migration for its tables.

## Evidence

A throwaway Django 5.2 project with only `contenttypes` and `sessions` in `INSTALLED_APPS` passed `check` and `migrate`, and `make_password` and `check_password` worked in it (checked in [#15](https://github.com/PorkChopExpress86/derbynet/issues/15)). On this server, `python manage.py migrate` on a fresh SQLite database creates `django_content_type`, `django_migrations`, `django_session`, and SQLite's own `sqlite_sequence`. The settings in `server/derbynet/settings.py` and the tests in `server/tests/test_pages.py` and `server/tests/test_passwords.py` carry the decision.
