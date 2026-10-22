class SiteEventRouter:
    """siteapp lives in `site`; eventapp lives in `event` (or a temporary `event_open_*` alias)."""

    def db_for_read(self, model, **hints):
        return "event" if model._meta.app_label == "eventapp" else "site"

    db_for_write = db_for_read

    def allow_relation(self, obj1, obj2, **hints):
        return obj1._meta.app_label == obj2._meta.app_label

    def allow_migrate(self, db, app_label, **hints):
        if app_label == "eventapp":
            return db.startswith("event")
        return db == "site"
