"""The timer's and the replay client's contact windows, judged on each leg's
own clock (#218).

Both servers call the timer connected for 60 s after its last contact
(`website/inc/timer-state.inc:72`, `racing.services.timer.CONTACT_TIMEOUT`)
and the replay client for 10 s (`REPLAY_TIMEOUT_LIMIT`,
`website/inc/replay.inc:29`, `racing.services.replay.REPLAY_TIMEOUT`). The
legs of the parity gate run at different speeds, so one leg's window can
close where the other's is still open. The gate then sees a
connected timer or replay against a NOT CONNECTED one, a `HELLO` that only
one replay poll sends, and the commands one leg queued while its replay was
connected and the other dropped.

`window_allowances` allows such a difference only when each leg's value is
one its own clock allows under the shared window. A leg's clock at a reply
is a range: from the latest server time it has shown up to then (a
`timecheck`, a last contact, or a last contact plus the age a connected
state shows) to the earliest it shows from then on in the step. The times
are whole seconds, so each bound carries a second's slack.

- A connected state shows its age, `(N sec.)`, and N must be within the
  window. A NOT CONNECTED state must have no contact, or a last contact the
  window may have passed by the leg's clock. Only the pair connected
  (state 2) and NOT CONNECTED (state 1) is a window difference; the lanes
  and remote start of the timer are still compared.
- A `HELLO` that one replay poll sends and the other doesn't: the leg that
  sends it may have had a closed window before the poll, and the leg that
  doesn't may have had an open one.
- One leg's queue a prefix of the other's: the shorter leg showed its
  replay NOT CONNECTED since its last poll, so `send` dropped what came
  after.

A value the leg's clock rules out still fails, as does anything else in
these replies.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime

# This module loads no Django, so the windows are copies, which
# tests/test_contact_windows.py holds to Django's and PHP's.
# `$this->last_contact + 60 < time()`, website/inc/timer-state.inc:72;
# racing.services.timer.CONTACT_TIMEOUT.
TIMER_WINDOW = 60
# REPLAY_TIMEOUT_LIMIT, website/inc/replay.inc:29;
# racing.services.replay.REPLAY_TIMEOUT.
REPLAY_WINDOW = 10

NOT_CONNECTED = 1
CONNECTED = 2

_AGE = re.compile(r"\((\d+) sec\.\)$")

# The state object, its last-contact key, its window, and the paths a window
# difference shows.
_STATES = {
    "timer-state": ("last-contact", TIMER_WINDOW, ("state", "icon", "message")),
    "replay-state": (
        "last_contact",
        REPLAY_WINDOW,
        ("state", "icon", "connected", "message"),
    ),
}


INFINITY = float("inf")


@dataclass
class Clock:
    """What one leg has shown of its time and its replay client."""

    # The latest server time the leg has shown, in whole epoch seconds.
    now: float | None = None
    # The range the replay client's last contact lies in, as the leg last
    # showed it: [epoch, epoch + 1) from a reply, or the clock's range at a
    # poll. None before any contact.
    replay_contact: tuple[float, float] | None = None
    # The leg showed its replay NOT CONNECTED since its last poll.
    replay_lapsed: bool = False

    def at(self, reply: dict) -> float | None:
        """The earliest time the leg can be at `reply`, a raw JSON value."""
        found = [*_marks(reply), *_contacts(reply)]
        if self.now is not None:
            found.append(self.now)
        return max(found) if found else None

    def observe(self, endpoint: str, body: str, latest: float = INFINITY) -> None:
        """Take in one exchange's raw reply, after it is judged. `latest` is
        the latest time the leg can be at it (`latest_times`)."""
        reply = _reply(body)
        if reply is None:
            return
        self.now = self.at(reply)
        replay = reply.get("replay-state")
        if isinstance(replay, dict):
            contact = replay.get("last_contact")
            if isinstance(contact, int | float) and contact > 0:
                self.replay_contact = (contact, contact + 1)
            if replay.get("state") == NOT_CONNECTED:
                self.replay_lapsed = True
        if endpoint == "action replay.message" and _succeeded(reply):
            self.replay_contact = (self.now or 0, latest)
            self.replay_lapsed = False

    def forget_replay(self) -> None:
        """A database reset: the replay state is gone on both servers."""
        self.replay_contact, self.replay_lapsed = None, False


def latest_times(bodies: list[str], after: float = INFINITY) -> list[float]:
    """For each exchange of one leg's step, the latest time the leg can be
    at it: before every time a later reply of the step shows, plus the
    slack of whole seconds. `after` bounds the step's last exchanges: the
    latest time the leg's later steps allow (`parity_gate.later_bounds`).
    Infinite with no later time, which excuses nothing."""
    out, latest = [], after
    for body in reversed(bodies):
        reply = _reply(body)
        if reply is not None:
            latest = min([latest, *(mark + 2 for mark in _marks(reply))])
        out.append(latest)
    return out[::-1]


def _reply(body: str):
    """The one JSON object of a reply, or None."""
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _succeeded(reply: dict) -> bool:
    outcome = reply.get("outcome")
    return isinstance(outcome, dict) and outcome.get("summary") == "success"


def _timecheck(text) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        return None


def _age(state: dict) -> int | None:
    found = _AGE.search(str(state.get("message", "")))
    return int(found[1]) if found else None


def _marks(reply: dict):
    """The server times a reply shows of when it was made: its `timecheck`,
    and a connected state's last contact plus its age."""
    stamp = _timecheck(reply.get("timecheck"))
    if stamp is not None:
        yield stamp
    for key, (contact_key, _, _) in _STATES.items():
        state = reply.get(key)
        if not isinstance(state, dict):
            continue
        contact, age = state.get(contact_key), _age(state)
        if isinstance(contact, int | float) and contact > 0 and age is not None:
            yield contact + age


def _contacts(reply: dict):
    """The last contacts a reply shows, each before the reply was made."""
    for key, (contact_key, _, _) in _STATES.items():
        state = reply.get(key)
        if isinstance(state, dict):
            contact = state.get(contact_key)
            if isinstance(contact, int | float) and contact > 0:
                yield contact


@dataclass(frozen=True)
class Verdict:
    """Whether a leg's state is one its clock allows, and why."""

    consistent: bool
    text: str


def _span(earliest: float, latest: float) -> str:
    if latest == INFINITY:
        return f"at least {earliest:.0f} s"
    return f"{earliest:.0f} to {latest:.0f} s"


def _judge_state(key: str, state: dict, earliest, latest: float) -> Verdict:
    contact_key, window, _ = _STATES[key]
    contact = state.get(contact_key)
    if not isinstance(contact, int | float) or contact <= 0 or earliest is None:
        # Never contacted, or no clock: the window explains nothing.
        return Verdict(False, f"state {state.get('state')} with no contact to judge")
    # The contact lies in [contact, contact + 1).
    youngest = max(earliest - contact - 1, 0)
    if state.get("state") == CONNECTED:
        age = _age(state)
        if age is None:
            return Verdict(False, "connected with no age shown")
        # Its own age, and the leg's clock, must both leave the window open.
        return Verdict(
            age <= window and youngest <= window,
            f"connected, last contact {age} s before",
        )
    if latest == INFINITY:
        return Verdict(False, "NOT CONNECTED with no later time to bound it")
    oldest = latest - contact
    span = _span(youngest, oldest)
    return Verdict(oldest > window, f"NOT CONNECTED, last contact {span} before")


def _state_allowances(raw_a, raw_b, clock_a, clock_b, latest_a, latest_b):
    allowed = {}
    for key, (_, window, fields) in _STATES.items():
        a, b = raw_a.get(key), raw_b.get(key)
        if not (isinstance(a, dict) and isinstance(b, dict)):
            continue
        if {a.get("state"), b.get("state")} != {CONNECTED, NOT_CONNECTED}:
            continue
        php = _judge_state(key, a, clock_a.at(raw_a), latest_a)
        django = _judge_state(key, b, clock_b.at(raw_b), latest_b)
        if php.consistent and django.consistent:
            reason = (
                f"contact window ({window} s, on each leg's own clock): PHP"
                f" {php.text}; Django {django.text}"
            )
            for name in fields:
                allowed[f"$.{key}.{name}"] = reason
    return allowed


def _replay_age(clock: Clock, latest: float) -> tuple[float, float] | None:
    """The range of the time since the replay's last contact at a poll, or
    None with no contact or no clock."""
    if clock.replay_contact is None or clock.now is None:
        return None
    low, high = clock.replay_contact
    return max(clock.now - high, 0), latest - low


def _queue_allowances(raw_a, raw_b, clock_a, clock_b, latest_a, latest_b):
    a, b = raw_a.get("replay"), raw_b.get("replay")
    if not (isinstance(a, list) and isinstance(b, list)) or a == b:
        return {}
    allowed = {}
    for hello, other, legs in (
        (a, b, (("PHP", clock_a, latest_a), ("Django", clock_b, latest_b))),
        (b, a, (("Django", clock_b, latest_b), ("PHP", clock_a, latest_a))),
    ):
        if not (hello[:1] == ["HELLO"] and other[:1] != ["HELLO"]):
            continue
        if hello[1:] != other:
            continue
        (said, hello_clock, hello_latest), (quiet, other_clock, other_latest) = legs
        closed = _replay_age(hello_clock, hello_latest)
        opened = _replay_age(other_clock, other_latest)
        # The HELLO leg's window may have closed: a contact the window may
        # have passed by a bounded clock. The other's may still have been
        # open. A leg with no contact explains nothing.
        may_close = (
            closed is not None and closed[1] != INFINITY and closed[1] > REPLAY_WINDOW
        )
        may_open = opened is not None and opened[0] <= REPLAY_WINDOW
        if may_close and may_open:
            said_text = f"last replay contact {_span(*closed)} before"
            allowed["$.replay[0]"] = (
                f"contact window ({REPLAY_WINDOW} s, on each leg's own clock):"
                f" {said} says HELLO, {said_text}; {quiet}'s last replay"
                f" contact {_span(*opened)} before"
            )
    if (a[:1] == ["HELLO"]) != (b[:1] == ["HELLO"]):
        # Only one poll says HELLO: the queues line up only by the rule above.
        return allowed
    for short, long, short_clock, leg in (
        (a, b, clock_a, "PHP"),
        (b, a, clock_b, "Django"),
    ):
        if len(short) < len(long) and long[: len(short)] == short:
            if short_clock.replay_lapsed:
                for i in range(len(short), len(long)):
                    allowed[f"$.replay[{i}]"] = (
                        f"contact window ({REPLAY_WINDOW} s): {leg} showed its"
                        " replay NOT CONNECTED since its last poll, so it"
                        " queued none of the commands after"
                    )
    return allowed


def window_allowances(
    endpoint: str,
    body_a: str,
    body_b: str,
    clock_a: Clock,
    clock_b: Clock,
    latest_a: float = INFINITY,
    latest_b: float = INFINITY,
) -> dict[str, str]:
    """The reply paths of one exchange whose difference the contact windows
    explain, each with why, from the two raw replies, each leg's clock
    before them, and the latest time each leg can be at them."""
    raw_a, raw_b = _reply(body_a), _reply(body_b)
    if raw_a is None or raw_b is None:
        return {}
    clocks = (clock_a, clock_b, latest_a, latest_b)
    allowed = _state_allowances(raw_a, raw_b, *clocks)
    if endpoint == "action replay.message":
        allowed.update(_queue_allowances(raw_a, raw_b, *clocks))
    return allowed
