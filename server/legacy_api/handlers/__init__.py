"""The `/action.php` handlers, one module per area, registered on import.

Handlers parse `ctx.args`, call services, and encode the result. They never
write through the ORM themselves (#17).
"""

from legacy_api.handlers import (  # noqa: F401
    database,
    roles,
    session,
    settings,
    testing,
)

testing.sync()
