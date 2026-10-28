"""The `/action.php` handlers, one module per area, registered on import.

Handlers parse `ctx.args`, call services, and encode the result. They never
write through the ORM themselves (#17). Every module in this package is
imported here, so a new area needs no edit to this file.
"""

import importlib
import pkgutil

for _module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module.name}")

from legacy_api.handlers import testing  # noqa: E402

testing.sync()
