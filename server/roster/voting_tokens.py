"""A racer's voting token as text (#206, D86): the vote link, `ballot.get`'s
`voting_token` and a Django snapshot's `votingtoken` column all carry it."""

import uuid

# Longer than any form `uuid.UUID` reads, so a long string is refused
# before it is parsed.
MAX_LENGTH = 64


def parse_token(text) -> uuid.UUID | None:
    """A voting token: a GUID in any of the forms `uuid.UUID` reads, or None
    for anything else."""
    if not isinstance(text, str) or len(text) > MAX_LENGTH:
        return None
    try:
        return uuid.UUID(text)
    except ValueError:
        return None
