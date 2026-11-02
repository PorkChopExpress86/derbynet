"""The default roles' logins, for the UI tests (#40)."""

import json
from pathlib import Path

from accounts.defaults import DEFAULT_ROLES

# Role name to password. "anonymous" is no login.
PASSWORDS = {name: password for name, password, _, _ in DEFAULT_ROLES if name}
ROLES = ("anonymous", *PASSWORDS)
ROLE_NAV = json.loads((Path(__file__).parent / "data" / "role_nav.json").read_text())
