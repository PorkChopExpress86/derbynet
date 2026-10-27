"""A cut with each racer once, as `new_round_roster` enters it."""

from racing.engine.standings.finalists import Bucket, Cut, Finalist


def test_a_racer_in_two_buckets_counts_in_the_first_only():
    cut = Cut(
        (
            Bucket("Lions", (Finalist(1, 3.0), Finalist(2, 3.1))),
            Bucket("Finals", (Finalist(2, 3.1), Finalist(3, 3.2))),
        ),
        limit=2,
    )
    assert cut.concerns == ()
    unique = cut.unique()
    assert [f.racer_id for f in unique.finalists] == [1, 2, 3]
    assert unique.concerns == ("There are only 1 eligible Finals racer(s).",)


def test_a_bucket_that_reaches_its_limit_has_no_concern():
    """PHP's `count($bucket) < $limit`: a top 0 cut is never short."""
    cut = Cut((Bucket("Lions", ()), Bucket("Tigers", (Finalist(1, 3.0),))), limit=0)
    assert cut.concerns == ()
    assert Cut((Bucket("Lions", ()),), limit=1).concerns == (
        "There are no eligible Lions racer(s).",
    )
