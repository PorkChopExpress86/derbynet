from django.apps import AppConfig


class LegacyApiConfig(AppConfig):
    name = "legacy_api"

    def ready(self):
        # Importing the handler modules registers them.
        from legacy_api import handlers  # noqa: F401
