import pytest

from accounts.permissions import Access, Perm, allows, granted, role


def test_the_bits_are_phps():
    assert {p.name: p.value for p in Perm} == {
        "VIEW_RACE_RESULTS": 1,
        "VIEW_AWARDS": 2,
        "CHECK_IN_RACERS": 4,
        "REVERT_CHECK_IN": 8,
        "REGISTER_NEW_RACER": 32,
        "EDIT_RACER": 64,
        "ASSIGN_RACER_IMAGE": 128,
        "JUDGING": 256,
        "CONTROL_RACE": 512,
        "PRESENT_AWARDS": 1024,
        "EDIT_AWARDS": 2048,
        "TIMER_MESSAGE": 4096,
        "PHOTO_UPLOAD": 8192,
        "SET_UP": 32768,
        "ADMINISTRATION": 65536,
    }


def test_an_empty_session_holds_nothing():
    assert granted({}) == 0
    assert role({}) is None
    assert role({"role": ""}) is None


@pytest.mark.parametrize(
    ("bits", "requirement", "expected"),
    [
        (0, Perm.CONTROL_RACE, False),
        (int(Perm.CONTROL_RACE), Perm.CONTROL_RACE, True),
        (int(Perm.SET_UP), Perm.CONTROL_RACE, False),
        (-1, Perm.ADMINISTRATION, True),
        (int(Perm.PHOTO_UPLOAD), Perm.CHECK_IN_RACERS | Perm.PHOTO_UPLOAD, True),
        (0, Access.ANONYMOUS, True),
    ],
)
def test_a_requirement_is_met_by_any_of_its_bits(bits, requirement, expected):
    assert allows({"permissions": bits}, requirement) is expected


def test_logged_in_needs_a_role_name_not_bits():
    assert not allows({"permissions": -1}, Access.LOGGED_IN)
    assert allows({"role": "Timer", "permissions": 4096}, Access.LOGGED_IN)
