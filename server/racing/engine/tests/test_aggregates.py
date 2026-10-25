import itertools

import pytest

from racing.engine.groups.aggregates import (
    Aggregate,
    constituents_closure,
    ready_aggregates,
    topological_order,
)


def ids(aggregates):
    return [a.group_id for a in aggregates]


def assert_topological(order):
    position = {a.group_id: i for i, a in enumerate(order)}
    for a in order:
        for c in a.constituents:
            if c in position:
                assert position[a.group_id] < position[c], (a.group_id, c)


def test_an_aggregate_of_the_first_of_two_unrelated_groups_moves_ahead_of_it():
    """The Q8 case. PHP's usort leaves [A, B, Z] as it is, because its
    comparator calls A and B equal and B and Z equal."""
    a, b = Aggregate(1, (10, 11)), Aggregate(2, (12,))
    z = Aggregate(3, (1,))
    assert ids(topological_order([a, b, z])) == [2, 3, 1]


def test_a_chain_comes_out_from_the_top_down():
    bottom, middle, top = (
        Aggregate(1, (10,)),
        Aggregate(2, (1,)),
        Aggregate(3, (2,)),
    )
    assert ids(topological_order([bottom, middle, top])) == [3, 2, 1]


def test_a_diamond_puts_the_top_first_and_the_shared_constituent_last():
    left, right = Aggregate(2, (1,)), Aggregate(3, (1,))
    shared, top = Aggregate(1, (10,)), Aggregate(4, (2, 3))
    assert ids(topological_order([shared, left, right, top])) == [4, 2, 3, 1]


def test_unrelated_aggregates_keep_their_input_order():
    aggregates = [Aggregate(i, (100 + i,)) for i in (5, 3, 9)]
    assert ids(topological_order(aggregates)) == [5, 3, 9]


def test_every_input_order_of_a_graph_gives_a_topological_order():
    graph = [
        Aggregate(1, (10, 11)),
        Aggregate(2, (1, 12)),
        Aggregate(3, (1,)),
        Aggregate(4, (2, 3)),
        Aggregate(5, (13,)),
    ]
    for perm in itertools.permutations(graph):
        order = topological_order(list(perm))
        assert sorted(ids(order)) == [1, 2, 3, 4, 5]
        assert_topological(order)


def test_a_cycle_is_an_error():
    with pytest.raises(ValueError):
        topological_order([Aggregate(1, (2,)), Aggregate(2, (1,))])


def test_closure_follows_aggregates_of_aggregates():
    younger, older = Aggregate(7, (1, 2)), Aggregate(8, (3, 4, 5))
    scouts = Aggregate(9, (7, 8))
    final = Aggregate(10, (9, 6))
    closure = constituents_closure([younger, older, scouts, final])
    assert closure == {
        7: [1, 2],
        8: [3, 4, 5],
        9: [7, 8, 1, 2, 3, 4, 5],
        10: [9, 6, 7, 8, 1, 2, 3, 4, 5],
    }


def test_closure_of_a_diamond_lists_the_shared_group_once():
    closure = constituents_closure(
        [Aggregate(2, (1,)), Aggregate(3, (1,)), Aggregate(4, (2, 3))]
    )
    assert closure[4] == [2, 3, 1]


def test_ready_needs_every_constituent_finished():
    """`http/suite` has no capture of this; `testing/test-aggregate-nonracing.sh`
    builds the same graph. Younger is ready while Tigers waits, because
    Tigers has no heats of its own, as in PHP."""
    tigers = Aggregate(8, (1, 6))
    younger = Aggregate(9, (2, 8))
    assert ready_aggregates([tigers, younger], racing={1, 2, 6}, unfinished={6}) == [9]
    assert (
        ready_aggregates([tigers, younger], racing={1, 2, 6}, unfinished={1, 2}) == []
    )
    assert ready_aggregates([tigers, younger], racing={1, 2, 6}, unfinished={2}) == [8]
    assert ready_aggregates([tigers, younger], racing={1, 2, 6}, unfinished=()) == [
        8,
        9,
    ]


def test_a_constituent_that_was_never_scheduled_counts_as_ready():
    assert ready_aggregates([Aggregate(8, (1, 2))], racing={1}, unfinished=()) == [8]


def test_an_aggregate_with_a_round_is_not_offered():
    assert ready_aggregates([Aggregate(8, (1,))], racing={1, 8}, unfinished=()) == []


def test_ready_aggregates_come_in_the_order_given():
    later, earlier = Aggregate(9, (1,)), Aggregate(8, (2,))
    assert ready_aggregates([later, earlier], racing={1, 2}, unfinished=()) == [9, 8]


def test_an_aggregate_drawing_from_a_ready_aggregate_is_ready_too():
    """`unfinished` cannot name an aggregate with no round, but the pass
    still treats a ready constituent as finished, as PHP does."""
    inner, outer = Aggregate(8, (1,)), Aggregate(9, (8,))
    assert ready_aggregates([outer, inner], racing={1}, unfinished={8}) == [8, 9]
