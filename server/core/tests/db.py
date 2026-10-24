from contextlib import contextmanager

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import Max


@contextmanager
def refused():
    """Assert that the database refuses the writes in the block.

    Foreign keys are deferred to commit on both engines, so the block ends by
    checking them. The savepoint rolls the refused write back.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            yield
            connection.check_constraints()


def missing_id(model):
    """An id that no row of `model` has."""
    return (model.objects.aggregate(m=Max("id"))["m"] or 0) + 1000
