"""The roles and passwords `write_config_roles_file()` writes for a new PHP
install, so `testing/default.passwords` stays true (#15, D16).

The seed migration and `accounts.services.roles_with_default_password()` both
read this one list.
"""

# (name, password, permissions, interactive). RaceCrew's 8687 is
# VIEW_RACE_RESULTS | VIEW_AWARDS | CHECK_IN_RACERS | REVERT_CHECK_IN
# | REGISTER_NEW_RACER | EDIT_RACER | ASSIGN_RACER_IMAGE | JUDGING | PHOTO_UPLOAD.
DEFAULT_ROLES = (
    ("", "", 1, True),
    ("Timer", "", 4096, False),
    ("Photo", "flashbulb", 8192, False),
    ("RaceCrew", "murphy", 8687, True),
    ("RaceCoordinator", "doyourbest", -1, True),
)
POST_SETUP_ROLE = "RaceCoordinator"
