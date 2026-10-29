"""One test per row of `heat_state.TABLE`, and a check that every event
meets a row in every kind of state."""

import pytest

from racing.engine.heat_state import (
    TABLE,
    TIMER_TEST_ROUND,
    Advance,
    ApplyRacingScene,
    By,
    Cancel,
    CaptureLastHeat,
    Chart,
    ClearLastHeat,
    ClearResults,
    ClearTimerCurrent,
    Code,
    Kind,
    ManualResult,
    Moved,
    PlaylistHook,
    RacingOff,
    RacingOn,
    Reinstate,
    Replay,
    Rerun,
    RestoreLastHeat,
    Select,
    SendReplay,
    State,
    TimerResult,
    TimerTestEnd,
    TimerTestStart,
    TimerToConnected,
    WriteResults,
    WriteTestResults,
    always,
    decide,
    find_row,
)

CANCEL = SendReplay(Replay.CANCEL)
REPLAY = SendReplay(Replay.REPLAY)


def start(round_id, heat):
    return SendReplay(Replay.START, (round_id, heat))


def chart(*heats, master=False, emptied=()):
    """Heats as `(roundid, heat, results)`, where `results` is a string of
    `x` (a result) and `.` (none) per lane. Master numbers count up in the
    order given."""
    lanes = tuple(
        (n, r, h, lane, mark == "x")
        for n, (r, h, marks) in enumerate(heats, start=1)
        for lane, mark in enumerate(marks, start=1)
    )
    return Chart(lanes, tuple(emptied), master)


# Round 7 has heats 1 to 3 on two lanes; heat 1 has run.
ROUND = chart((7, 1, "xx"), (7, 2, ".."), (7, 3, ".."))
RACING_2 = State(round_id=7, heat=2, racing=True)
IDLE_2 = State(round_id=7, heat=2)


def assert_row(state, event, chart, name):
    assert find_row(state, event, chart).name == name


# ----- racing on and off -----


def test_racing_on_from_a_round_button_when_not_allowed():
    state = State(round_id=7, heat=1, refused=2)
    decision = decide(state, RacingOn(By.ROUND_BUTTON), ROUND)
    assert decision.failure.code is Code.CANT_RACE
    assert "already been run" in decision.failure.description
    assert decision.state == state
    assert decision.effects == ()


def test_racing_on_when_not_allowed_clears_the_refused_count():
    decision = decide(State(round_id=7, heat=1, refused=2), RacingOn(By.MANAGER), ROUND)
    assert decision.failure.code is Code.CANT_RACE
    assert decision.state == State(round_id=7, heat=1, refused=0)


def test_racing_on_by_the_server_when_not_allowed_changes_nothing():
    state = State(round_id=7, heat=1, refused=2)
    for by in (By.RERUN, By.PLAYLIST, By.WAITING_STEP):
        decision = decide(state, RacingOn(by), ROUND)
        assert (decision.state, decision.effects, decision.failure) == (state, (), None)


def test_racing_on_sends_start_and_applies_the_racing_scene():
    state = State(round_id=7, heat=2, refused=3, intermission=True)
    decision = decide(state, RacingOn(By.MANAGER), ROUND)
    assert decision.state == State(round_id=7, heat=2, racing=True)
    assert decision.effects == (start(7, 2), ApplyRacingScene())


def test_racing_on_from_the_playlist_keeps_the_scene_and_the_intermission():
    state = State(round_id=7, heat=2, intermission=True)
    decision = decide(state, RacingOn(By.PLAYLIST), ROUND)
    assert decision.state == State(round_id=7, heat=2, racing=True, intermission=True)
    assert decision.effects == (start(7, 2),)


def test_racing_on_for_a_rerun_resets_the_timer():
    decision = decide(IDLE_2, RacingOn(By.RERUN), ROUND)
    assert decision.effects == (start(7, 2), ApplyRacingScene(), TimerToConnected())


def test_racing_on_when_already_on_sends_no_start():
    decision = decide(State(7, 2, racing=True, refused=1), RacingOn(By.MANAGER), ROUND)
    assert decision.state == RACING_2
    assert decision.effects == (ApplyRacingScene(),)


def test_racing_on_in_a_timer_test_is_always_allowed():
    state = State(round_id=TIMER_TEST_ROUND, heat=4)
    decision = decide(state, RacingOn(By.MANAGER), Chart())
    assert decision.state.racing
    assert decision.effects[0] == start(TIMER_TEST_ROUND, 4)


def test_racing_off_sends_cancel():
    decision = decide(State(7, 2, racing=True, refused=4), RacingOff(), ROUND)
    assert decision.state == IDLE_2
    assert decision.effects == (CANCEL,)


def test_racing_off_when_off_sends_nothing():
    decision = decide(State(7, 2, refused=4), RacingOff(), ROUND)
    assert decision.state == IDLE_2
    assert decision.effects == ()


# ----- moving the current heat -----


def test_select_turns_racing_off_and_moves():
    state = State(round_id=7, heat=2, racing=True, intermission=True)
    decision = decide(state, Select(7, 3), ROUND)
    assert decision.state == State(round_id=7, heat=3)
    assert decision.effects == (CANCEL, CANCEL, Moved(7, 3))
    assert decision.failure is None


def test_select_of_a_missing_heat_fails_with_nosuchheat():
    decision = decide(RACING_2, Select(7, 9), ROUND)
    assert decision.state == IDLE_2
    assert decision.failure.code is Code.NO_SUCH_HEAT
    assert decision.failure.description == "No heat 9 scheduled for roundid 7."
    assert decision.effects == (CANCEL,)


def test_next_with_no_next_heat_after_a_complete_round_runs_the_playlist_hook():
    done = chart((7, 1, "xx"), (7, 2, "xx"))
    decision = decide(State(7, 2, racing=True), Advance("next"), done)
    assert decision.effects == (CANCEL, PlaylistHook(7))
    assert decision.state == IDLE_2
    assert decision.failure is None


def test_a_reported_advance_with_no_heat_fails():
    decision = decide(State(7, 3), Advance("next", report=True), ROUND)
    assert decision.failure.code is Code.NO_SUCH_HEAT
    assert decision.effects == ()


@pytest.mark.parametrize("requested", ["prev", "first", 2])
def test_only_next_runs_the_playlist_hook(requested):
    """Q2: PHP's assignment made every request look like `next`."""
    done = chart((7, 1, "xx"), (7, 2, "xx"))
    state = State(7, 2, racing=True)
    decision = decide(state, Advance(requested), done)
    assert not any(isinstance(e, PlaylistHook) for e in decision.effects)
    assert not decision.state.racing


def test_next_onto_a_raced_heat_of_a_complete_round_runs_the_playlist_hook():
    done = chart((7, 1, "xx"), (7, 2, "xx"), (7, 3, "xx"))
    decision = decide(State(7, 2, racing=True), Advance("next"), done)
    assert decision.effects == (CANCEL, Moved(7, 3), CANCEL, PlaylistHook(7))
    assert decision.state == State(7, 3)


def test_next_onto_a_raced_heat_turns_racing_off():
    raced = chart((7, 1, ".."), (7, 2, ".."), (7, 3, "xx"))
    decision = decide(State(7, 2, racing=True), Advance("next"), raced)
    assert decision.effects == (CANCEL, Moved(7, 3), CANCEL)
    assert decision.state == State(7, 3)


def test_next_while_racing_starts_the_next_heat():
    decision = decide(State(7, 1, racing=True), Advance("next"), ROUND)
    assert decision.effects == (CANCEL, Moved(7, 2), start(7, 2))
    assert decision.state == RACING_2


def test_advance_with_racing_off_only_moves():
    decision = decide(State(7, 1), Advance("next"), ROUND)
    assert decision.effects == (CANCEL, Moved(7, 2))
    assert decision.state == IDLE_2


def test_cancel_clears_the_current_heat():
    decision = decide(RACING_2, Cancel(), ROUND)
    assert decision.state == State()
    assert decision.effects == (CANCEL, Moved(None, None))


def test_next_lands_on_a_held_emptied_heat():
    """D39: the race crew runs or skips it by hand, so it is never lost."""
    emptied = Chart(ROUND.lanes, ((0, 7, 4),))
    decision = decide(State(7, 3, racing=True), Advance("next"), emptied)
    assert decision.effects == (CANCEL, Moved(7, 4), CANCEL)
    assert decision.state == State(7, 4)
    assert decide(State(7, 4), Advance("next", report=True), emptied).failure


def test_master_schedule_next_follows_master_numbers_across_rounds():
    master = chart((7, 1, "xx"), (8, 1, ".."), (7, 2, ".."), master=True)
    decision = decide(State(7, 1, racing=True), Advance("next"), master)
    assert decision.effects == (CANCEL, Moved(8, 1), start(8, 1))


def test_master_schedule_next_up_reaches_a_held_emptied_heat():
    lanes = ((1, 7, 1, 1, True), (3, 8, 1, 1, False))
    master = Chart(lanes, ((2, 9, 3),), use_master_sched=True)
    decision = decide(State(7, 1), Advance("next-up"), master)
    assert decision.state == State(9, 3)


def test_a_heat_number_ignores_the_master_schedule():
    master = chart((7, 1, "xx"), (8, 1, ".."), (7, 2, ".."), master=True)
    assert decide(State(7, 1), Advance(2), master).state == State(7, 2)


def test_timer_test_next_needs_no_chart():
    state = State(round_id=TIMER_TEST_ROUND, heat=1, racing=True)
    decision = decide(state, Advance("next"), Chart(use_master_sched=True))
    assert decision.state == State(TIMER_TEST_ROUND, 2, racing=True)


# ----- results -----


WRITTEN = ((1, True), (2, True))


def test_timer_result_with_racing_off_is_refused_and_counted():
    decision = decide(State(7, 2, refused=1), TimerResult(written=WRITTEN), ROUND)
    assert decision.failure.code is Code.NOT_RACING
    assert decision.state == State(7, 2, refused=2)
    assert decision.effects == ()


def test_all_zero_timer_result_replays_and_stops_racing():
    decision = decide(RACING_2, TimerResult(all_zero=True), ROUND)
    assert decision.effects == (REPLAY, CANCEL)
    assert decision.state == IDLE_2


def test_timer_result_in_a_timer_test_writes_the_test_and_moves_on():
    state = State(round_id=TIMER_TEST_ROUND, heat=1, racing=True)
    decision = decide(state, TimerResult(written=WRITTEN), Chart())
    assert decision.effects == (
        WriteTestResults(1),
        REPLAY,
        ClearTimerCurrent(),
        CANCEL,
        Moved(TIMER_TEST_ROUND, 2),
        start(TIMER_TEST_ROUND, 2),
    )


def test_timer_result_with_no_current_round_fails():
    decision = decide(State(racing=True), TimerResult(written=WRITTEN), ROUND)
    assert decision.failure.code is Code.NO_CURRENT_ROUND
    assert decision.effects == ()


@pytest.mark.parametrize(
    ("posted_round", "posted_heat", "wrong"),
    [("7", "9", True), ("8", None, True), ("0", "0", False), ("7", "2", False)],
)
def test_timer_result_for_another_heat_fails(posted_round, posted_heat, wrong):
    event = TimerResult(
        written=WRITTEN, posted_round=posted_round, posted_heat=posted_heat
    )
    decision = decide(RACING_2, event, ROUND)
    assert (decision.failure is not None) is wrong
    if wrong:
        assert decision.failure.code is Code.WRONG_HEAT
        assert decision.failure.description == (
            "Expecting heat results for roundid 7 and heat 2"
        )


def test_timer_result_writes_replays_and_advances():
    decision = decide(RACING_2, TimerResult(written=WRITTEN), ROUND)
    assert decision.effects == (
        WriteResults((7, 2)),
        REPLAY,
        ClearTimerCurrent(),
        CANCEL,
        Moved(7, 3),
        start(7, 3),
    )
    assert decision.state == State(7, 3, racing=True)


def test_the_last_timer_result_of_a_round_runs_the_playlist_hook():
    last = chart((7, 1, "xx"), (7, 2, "xx"), (7, 3, ".."))
    decision = decide(State(7, 3, racing=True), TimerResult(written=WRITTEN), last)
    assert decision.effects[-2:] == (CANCEL, PlaylistHook(7))
    assert decision.state == State(7, 3)


def test_a_partial_timer_result_leaves_the_round_unfinished():
    """Q11: a lane left without a result keeps the round open."""
    last = chart((7, 1, "xx"), (7, 2, "xx"), (7, 3, ".."))
    decision = decide(State(7, 3, racing=True), TimerResult(written=((1, True),)), last)
    assert PlaylistHook(7) not in decision.effects


def test_manual_result_for_the_shown_heat_after_it_moved_fails():
    decision = decide(RACING_2, ManualResult(expected=(7, 1), written=WRITTEN), ROUND)
    assert decision.failure.code is Code.WRONG_HEAT
    assert decision.effects == ()


def test_manual_result_in_a_racing_timer_test_advances():
    state = State(round_id=TIMER_TEST_ROUND, heat=1, racing=True)
    decision = decide(state, ManualResult(written=WRITTEN), Chart())
    assert decision.effects[0] == WriteTestResults(1)
    assert decision.state.heat == 2


def test_manual_result_in_a_timer_test():
    state = State(round_id=TIMER_TEST_ROUND, heat=1)
    decision = decide(state, ManualResult(written=WRITTEN), Chart())
    assert decision.effects == (WriteTestResults(1),)


def test_manual_result_with_no_current_round_fails():
    decision = decide(State(), ManualResult(written=WRITTEN), ROUND)
    assert decision.failure.code is Code.NO_CURRENT_ROUND


def test_manual_result_for_another_posted_heat_fails():
    event = ManualResult(written=WRITTEN, posted_heat="3")
    assert decide(IDLE_2, event, ROUND).failure.code is Code.WRONG_HEAT


def test_manual_result_while_racing_advances():
    decision = decide(RACING_2, ManualResult(written=WRITTEN), ROUND)
    assert decision.effects == (WriteResults((7, 2)), CANCEL, Moved(7, 3), start(7, 3))


def test_manual_result_with_racing_off_only_writes():
    decision = decide(IDLE_2, ManualResult(expected=(7, 2), written=WRITTEN), ROUND)
    assert decision.effects == (WriteResults((7, 2)),)
    assert decision.state == IDLE_2


# ----- rerun and reinstate -----


def test_rerun_of_a_missing_heat_fails():
    decision = decide(RACING_2, Rerun((7, 9)), ROUND)
    assert decision.failure.code is Code.NO_SUCH_HEAT
    assert decision.effects == ()
    assert decide(RACING_2, Rerun(None), ROUND).failure.code is Code.NO_SUCH_HEAT


def test_rerun_saves_and_clears_the_heat_and_races_it_again():
    decision = decide(State(7, 3, racing=True), Rerun((7, 1)), ROUND)
    assert decision.effects == (
        CANCEL,
        CANCEL,
        Moved(7, 1),
        start(7, 1),
        CaptureLastHeat((7, 1)),
        ClearResults((7, 1)),
        start(7, 1),
        ApplyRacingScene(),
        TimerToConnected(),
    )
    assert decision.state == State(7, 1, racing=True, last_saved=True)


def test_reinstate_restores_the_saved_heat_and_stops_racing():
    decision = decide(State(7, 2, racing=True, last_saved=True), Reinstate(), ROUND)
    assert decision.effects == (RestoreLastHeat(), ClearLastHeat(), CANCEL)
    assert decision.state == State(7, 2, last_saved=True)


def test_reinstate_with_nothing_saved_still_clears_and_stops_racing():
    decision = decide(RACING_2, Reinstate(), ROUND)
    assert decision.effects == (ClearLastHeat(), CANCEL)


# ----- the table as a whole -----


DONE = chart((7, 1, "xx"), (7, 2, "xx"))
RAN_3 = chart((7, 1, "xx"), (7, 2, "xx"), (7, 3, "xx"))
TEST_HEAT = State(TIMER_TEST_ROUND, 1)
TEST_RACING = State(TIMER_TEST_ROUND, 1, racing=True)

# A state, an event and a chart that land on each row.
ROW_CASES = {
    "racing on from a round's race button, not allowed": (
        State(7, 1),
        RacingOn(By.ROUND_BUTTON),
        ROUND,
    ),
    "racing on, not allowed": (State(7, 1), RacingOn(By.MANAGER), ROUND),
    "racing on by the server, not allowed": (State(7, 1), RacingOn(By.RERUN), ROUND),
    "racing on": (IDLE_2, RacingOn(By.MANAGER), ROUND),
    "racing on, already on": (RACING_2, RacingOn(By.MANAGER), ROUND),
    "racing off": (RACING_2, RacingOff(), ROUND),
    "racing off, already off": (IDLE_2, RacingOff(), ROUND),
    "select heat": (IDLE_2, Select(7, 3), ROUND),
    "advance next, no next heat, round complete": (
        State(7, 2, racing=True),
        Advance("next"),
        DONE,
    ),
    "advance, no such heat": (State(7, 3), Advance("next"), ROUND),
    "advance next while racing, next heat not allowed, round complete": (
        State(7, 2, racing=True),
        Advance("next"),
        RAN_3,
    ),
    "advance while racing, next heat not allowed": (
        State(7, 2, racing=True),
        Advance("prev"),
        ROUND,
    ),
    "advance while racing": (State(7, 1, racing=True), Advance("next"), ROUND),
    "advance, racing off": (State(7, 1), Advance("next"), ROUND),
    "cancel the running round": (RACING_2, Cancel(), ROUND),
    "timer result, racing off": (IDLE_2, TimerResult(), ROUND),
    "all-zero timer result": (RACING_2, TimerResult(all_zero=True), ROUND),
    "timer result in a timer test": (TEST_RACING, TimerResult(), Chart()),
    "timer result, no current round": (State(racing=True), TimerResult(), ROUND),
    "timer result for another heat": (
        RACING_2,
        TimerResult(posted_heat="3"),
        ROUND,
    ),
    "timer result": (RACING_2, TimerResult(written=WRITTEN), ROUND),
    "manual result for the shown heat, heat moved": (
        RACING_2,
        ManualResult(expected=(7, 1)),
        ROUND,
    ),
    "manual result in a timer test, racing": (TEST_RACING, ManualResult(), Chart()),
    "manual result in a timer test": (TEST_HEAT, ManualResult(), Chart()),
    "manual result, no current round": (State(), ManualResult(), ROUND),
    "manual result for another heat": (IDLE_2, ManualResult(posted_round="8"), ROUND),
    "manual result while racing": (RACING_2, ManualResult(written=WRITTEN), ROUND),
    "manual result": (IDLE_2, ManualResult(written=WRITTEN), ROUND),
    "rerun, no such heat": (RACING_2, Rerun((7, 9)), ROUND),
    "rerun": (RACING_2, Rerun((7, 1)), ROUND),
    "reinstate": (State(7, 2, last_saved=True), Reinstate(), ROUND),
    "reinstate, none saved": (IDLE_2, Reinstate(), ROUND),
    "timer test start": (RACING_2, TimerTestStart(3), ROUND),
    "timer test end": (TEST_RACING, TimerTestEnd((7, 2)), ROUND),
    "timer test end, not testing": (RACING_2, TimerTestEnd((7, 1)), ROUND),
}


def test_timer_test_start_races_its_heat_with_no_replay():
    state = State(7, 2, racing=True, refused=3)
    decision = decide(state, TimerTestStart(3), ROUND)
    assert decision.state == State(TIMER_TEST_ROUND, 3, racing=True)
    assert decision.effects == (Moved(TIMER_TEST_ROUND, 3),)


def test_timer_test_end_goes_back_to_the_saved_heat_with_racing_off():
    state = State(TIMER_TEST_ROUND, 4, racing=True, refused=1)
    decision = decide(state, TimerTestEnd((7, 2)), ROUND)
    assert decision.state == State(7, 2, refused=1)
    assert decision.effects == (Moved(7, 2),)


def test_timer_test_end_with_nothing_saved_leaves_no_current_heat():
    decision = decide(
        State(TIMER_TEST_ROUND, 4, racing=True), TimerTestEnd(None), ROUND
    )
    assert decision.state == State()


def test_timer_test_end_outside_the_test_changes_nothing():
    decision = decide(RACING_2, TimerTestEnd((7, 1)), ROUND)
    assert (decision.state, decision.effects) == (RACING_2, ())


def test_every_row_has_a_case():
    names = [row.name for row in TABLE]
    assert len(names) == len(set(names))
    assert sorted(ROW_CASES) == sorted(names)


@pytest.mark.parametrize("name", list(ROW_CASES))
def test_each_case_lands_on_its_row(name):
    state, event, case_chart = ROW_CASES[name]
    assert_row(state, event, case_chart, name)


def test_every_event_ends_in_a_row_that_always_matches():
    """So every state and event pair meets a row: a transition or an
    explicit refusal."""
    events = {row.event for row in TABLE}
    for event in events:
        rows = [row for row in TABLE if row.event is event]
        assert rows[-1].guard is always, event.__name__
        assert all(row.guard is not always for row in rows[:-1]), event.__name__


STATES = {
    (Kind.NONE, False): State(),
    (Kind.NONE, True): State(racing=True),
    (Kind.TIMER_TEST, False): State(TIMER_TEST_ROUND, 1),
    (Kind.TIMER_TEST, True): State(TIMER_TEST_ROUND, 1, racing=True),
    (Kind.HEAT, False): IDLE_2,
    (Kind.HEAT, True): RACING_2,
}
EVENTS = [
    *(RacingOn(by) for by in By),
    RacingOff(),
    Select(7, 1),
    Select(None, "next"),
    *(Advance(r) for r in ("first", "next", "prev", "next-up", 3)),
    Cancel(),
    TimerResult(written=WRITTEN),
    TimerResult(all_zero=True),
    ManualResult(written=WRITTEN),
    ManualResult(expected=(7, 2), written=WRITTEN),
    Rerun((7, 1)),
    Rerun(None),
    Reinstate(),
    TimerTestStart(2),
    TimerTestEnd((7, 2)),
    TimerTestEnd(None),
]


@pytest.mark.parametrize("master", [False, True])
@pytest.mark.parametrize("kind", list(STATES), ids=lambda k: f"{k[0]}-{k[1]}")
def test_every_state_and_event_pair_decides(kind, master):
    state = STATES[kind]
    assert state.kind is kind[0]
    for event in EVENTS:
        decision = decide(
            state, event, chart(*((7, h, "..") for h in (1, 2, 3)), master=master)
        )
        assert isinstance(decision.state, State)
