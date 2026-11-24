"""Shared-password roles (ADR-07, #15).

Roles are site-level, as `config-roles.inc` is: one set for every event.
"""

from django.contrib.auth.hashers import check_password, make_password
from django.db import models

from accounts.defaults import DEFAULT_ROLES

_SEEDED_PASSWORDS = {name: password for name, password, _, _ in DEFAULT_ROLES}


class RoleQuerySet(models.QuerySet):
    def named(self, name) -> Role | None:
        """The role called `name`, or None for a name no role can have: not a
        string, or holding a NUL, which Postgres refuses in a query."""
        if not isinstance(name, str) or "\x00" in name:
            return None
        return self.filter(name=name).first()


class Role(models.Model):
    # '' is the anonymous role: its bits are what a visitor with no login holds.
    name = models.CharField(max_length=100, unique=True, blank=True)
    # Empty means no password, as `Timer` has.
    password_hash = models.CharField(max_length=256, blank=True)
    # Whether the password is still the one a new install seeds the role
    # with (`accounts.defaults`), so the D16 banner hashes nothing (#441).
    # The default is the seeded state: the seed migration cannot name this
    # field, and the browser tests re-seed with it after each flush. Every
    # later writer of `password_hash` goes through `set_password`.
    keeps_seeded_password = models.BooleanField(default=True)
    # -1 holds every bit.
    permissions = models.BigIntegerField()
    # A role that only machines log in as is left off the login page.
    interactive = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField()
    # The role the first-run setup logs the session in as.
    post_setup = models.BooleanField(default=False)

    objects = RoleQuerySet.as_manager()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["post_setup"],
                condition=models.Q(post_setup=True),
                name="one_post_setup_role",
            ),
        ]

    def __str__(self):
        return self.name or "(anonymous)"

    def set_password(self, raw: str) -> None:
        self.password_hash = make_password(raw) if raw else ""
        self.keeps_seeded_password = raw == _SEEDED_PASSWORDS.get(self.name)

    def check_password(self, raw: str) -> bool:
        if not self.password_hash:
            return raw == ""
        return check_password(raw, self.password_hash)
