from django.db import models


class SiteState(models.Model):
    """Singleton row. `version` bumps on every switch so workers notice."""

    active_slug = models.CharField(max_length=64)
    version = models.IntegerField(default=0)
