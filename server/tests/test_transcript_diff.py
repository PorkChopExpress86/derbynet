"""The transcript differ of the parity gate (#37), on hand-written runs.

`testing/common.sh` writes each exchange as a blank line, the request line, a
blank line and the response body, so the fixtures here are in that form.
"""

import textwrap

from tools import transcript_diff as td


def run(text: str) -> str:
    return textwrap.dedent(text)


def differences(a: str, b: str, allow=None) -> td.Report:
    return td.diff(run(a), run(b), allow=allow)


IMPORT_AND_LIST = """
    login RaceCoordinator

    {{
        "outcome": {{"summary": "success", "code": "success", "description": ""}},
        "timecheck": "{timecheck}",
        "role": "RaceCoordinator"
    }}

    post action.php action=racer.import&firstname=Ann&lastname=Ames&partition=Lions

    {{"outcome": {{"summary": "success"}}, "racerid": {ann}}}

    post action.php action=racer.import&firstname=Bob&lastname=Burr&partition=Lions

    {{"outcome": {{"summary": "success"}}, "racerid": {bob}}}

    action.php?query=racer.list

    {{
        "racers": [
            {{"racerid": {ann}, "firstname": "Ann", "classid": {lions},
              "seq": {seq},
              "imagefile": "photo.php/head/file/cropped/ann.jpg/{breaker}",
              "carphoto": "photo.php/car/racer/{ann}/unrec-{breaker}",
              "checkin": "{stamp}"}},
            {{"racerid": {bob}, "firstname": "{bob_name}", "classid": {lions},
              "seq": 0, "imagefile": "photo.php/head/file/cropped/bob.jpg/0",
              "carphoto": "", "checkin": null}}
        ]
    }}

    post action.php action=racer.delete&racer={bob}

    {{"action": {{"action": "racer.delete", "racer": "{bob}"}},
      "outcome": {{"summary": "success"}}}}

    post action.php action=racer.bulk&what=number&who=c{lions}&start=501

    {{"action": {{"action": "racer.bulk", "who": "c{lions}"}},
      "outcome": {{"summary": "success"}}}}

    action.php?query=poll&values=timer-trouble,kiosks

    {{
        "timer-state": {{"last_contact": {epoch}, "state": "CONNECTED ({age} sec.)"}},
        "kiosks": [{{"address": "127.0.0.1+{port}", "age": {age},
                     "last-contact": {epoch}, "cookie": "sessionid={session}"}}],
        "cache-breaker": {breaker}
    }}

    photo car/racer/{ann}/{breaker} 0f3c

    0f3c

    photo head/file/original/Cub-3126.jpg/xyz

    """

FIRST = dict(
    timecheck="2026-11-01T01:39:28+0000",
    ann=1,
    bob=2,
    lions=1,
    seq=1730421568000,
    breaker=1730421500,
    stamp="2026-11-01 01:39:28",
    bob_name="Bob",
    epoch=1730421568,
    age=3,
    port=50122,
    session="0f3c9a",
)
# The same run on a server that handed out other ids, a second later.
SHIFTED = dict(
    FIRST,
    timecheck="2026-11-01T01:39:29+0000",
    ann=11,
    bob=12,
    lions=11,
    seq=1730421569000,
    breaker=1730421501,
    stamp="2026-11-01 01:39:29",
    epoch=1730421569,
    age=4,
    port=50177,
    session="77aa01",
)


def test_the_transcript_splits_into_requests_and_bodies():
    exchanges = td.parse(run(IMPORT_AND_LIST.format(**FIRST)))

    assert [e.request for e in exchanges] == [
        "login RaceCoordinator",
        "post action.php action=racer.import&firstname=Ann&lastname=Ames"
        "&partition=Lions",
        "post action.php action=racer.import&firstname=Bob&lastname=Burr"
        "&partition=Lions",
        "action.php?query=racer.list",
        "post action.php action=racer.delete&racer=2",
        "post action.php action=racer.bulk&what=number&who=c1&start=501",
        "action.php?query=poll&values=timer-trouble,kiosks",
        "photo car/racer/1/1730421500 0f3c",
        "photo head/file/original/Cub-3126.jpg/xyz",
    ]
    assert exchanges[1].body.strip().startswith('{"outcome"')
    # curl_photo_any records no body
    assert exchanges[-1].body.strip() == ""


def test_runs_that_differ_only_in_volatile_values_and_ids_match():
    report = differences(
        IMPORT_AND_LIST.format(**FIRST), IMPORT_AND_LIST.format(**SHIFTED)
    )

    assert report.ok, report.render()
    assert report.compared == 9


def test_a_changed_value_fails_with_its_endpoint_request_and_path():
    report = differences(
        IMPORT_AND_LIST.format(**FIRST),
        IMPORT_AND_LIST.format(**dict(SHIFTED, bob_name="Robert")),
    )

    assert not report.ok
    [found] = report.differences
    assert found.endpoint == "query racer.list"
    assert found.request == "action.php?query=racer.list"
    assert found.path == "$.racers[1].firstname"
    assert (found.a, found.b) == ("Bob", "Robert")
    rendered = report.render()
    assert "query racer.list" in rendered
    assert "$.racers[1].firstname" in rendered


def test_an_id_that_names_another_row_fails():
    # Bob's list entry names Ann's id: the same shape, but not the same rows.
    a = IMPORT_AND_LIST.format(**FIRST)
    b = IMPORT_AND_LIST.format(**SHIFTED).replace(
        '"racerid": 12, "firstname": "Bob"', '"racerid": 11, "firstname": "Bob"'
    )

    report = differences(a, b)

    assert [d.path for d in report.differences] == ["$.racers[1].racerid"]


def test_a_type_change_fails():
    a = IMPORT_AND_LIST.format(**FIRST)
    b = IMPORT_AND_LIST.format(**SHIFTED).replace('"racerid": 12}', '"racerid": "12"}')

    report = differences(a, b)

    [found] = report.differences
    assert found.path == "$.racerid"
    assert found.endpoint == "action racer.import"


def test_true_and_1_differ():
    # Python's True == 1, and 1 == 1.0, but a client can tell them apart.
    a = run("""
        action.php?query=poll

        {"reload": true, "lanes": 4}
        """)
    b = a.replace("true", "1").replace("4}", "4.0}")

    found = differences(a, b).differences

    assert [(d.path, d.a, d.b) for d in found] == [
        ("$.reload", True, 1),
        ("$.lanes", 4, 4.0),
    ]


def test_a_missing_key_fails():
    a = IMPORT_AND_LIST.format(**FIRST)
    b = run(IMPORT_AND_LIST.format(**SHIFTED)).replace(
        ',\n    "role": "RaceCoordinator"', ""
    )

    [found] = differences(a, b).differences

    assert found.path == "$.role"
    assert found.endpoint == "action role.login"
    assert found.b is td.MISSING


def test_a_zero_cache_breaker_is_not_masked():
    # 0 means the file is missing, which is not volatile.
    a = IMPORT_AND_LIST.format(**FIRST)
    b = IMPORT_AND_LIST.format(**SHIFTED).replace("bob.jpg/0", "bob.jpg/1730421501")

    [found] = differences(a, b).differences

    assert found.path == "$.racers[1].imagefile"


def test_a_request_mismatch_fails_and_stops():
    a = IMPORT_AND_LIST.format(**FIRST)
    b = IMPORT_AND_LIST.format(**SHIFTED).replace(
        "action=racer.delete&racer=12", "action=racer.delete&racer=11"
    )

    report = differences(a, b)

    assert not report.ok
    assert report.diverged is not None
    assert report.diverged.index == 5
    assert "racer.delete" in report.diverged.a
    assert report.compared == 4
    assert "diverge" in report.render()


def test_a_different_endpoint_is_a_request_mismatch():
    a = IMPORT_AND_LIST.format(**FIRST)
    b = IMPORT_AND_LIST.format(**SHIFTED).replace("query=racer.list", "query=racer.lst")

    report = differences(a, b)

    assert report.diverged.index == 4


def test_a_run_with_more_exchanges_fails():
    a = IMPORT_AND_LIST.format(**FIRST)
    b = run(IMPORT_AND_LIST.format(**SHIFTED)) + run("""
        action.php?query=role.list

        {"roles": []}
        """)

    report = differences(a, b)

    assert not report.ok
    assert report.diverged.index == 10
    assert report.diverged.a is None


def test_responses_glued_together_are_compared_one_by_one():
    # import-csv-roster.sh appends replies with no request line of their own.
    a = run("""
        post action.php action=racer.import&firstname=Ann

        {"racerid": 1}{"racerid": 2}{"outcome": "x"}
        """)
    b = a.replace('"x"', '"y"')

    [found] = differences(a, b).differences

    assert found.path == "[2]$.outcome"


TIMER = """
    post action.php action=timer-message&message=FINISHED&lane1=2.47

    <?xml version="1.0" encoding="UTF-8"?>
    <action-response action="timer-message" message="FINISHED" lane1="2.47">
      <success/>
      <heat-ready lane-mask="15" class="Wolves" round="1" roundid="{roundid}"
        heat="{heat}" lanes="4"/>
      <remote-log send="false"/>
    </action-response>

    post action.php action=timer-message&message=HEARTBEAT

    <?xml version="1.0" encoding="UTF-8"?>
    <action-response action="timer-message" message="HEARTBEAT">
      <failure code="nosuchheat">No heat 7 in roundid {roundid}</failure>
    </action-response>
    """


def test_xml_with_other_ids_matches():
    report = differences(
        TIMER.format(roundid=3, heat=2), TIMER.format(roundid=13, heat=2)
    )

    assert report.ok, report.render()
    assert report.compared == 2


def test_an_xml_difference_fails_with_its_path():
    report = differences(
        TIMER.format(roundid=3, heat=2), TIMER.format(roundid=13, heat=3)
    )

    [found] = report.differences
    assert found.endpoint == "action timer-message"
    assert found.path == "/action-response/heat-ready/@heat"
    assert (found.a, found.b) == ("2", "3")


def test_xml_against_json_fails():
    a = run(TIMER.format(roundid=3, heat=2))
    heartbeat = a[a.rindex("<?xml") :]
    b = a.replace(heartbeat, '{"outcome": {"summary": "failure"}}\n')

    report = differences(a, b)

    [found] = report.differences
    assert found.path == "$"
    assert found.index == 2
    assert (found.a, found.b) == ("<xml>", "<json>")


def test_pages_are_not_compared():
    a = run("""
        kiosk.php?address=one

        <html><body>PHP</body></html>
        """)
    b = a.replace("PHP", "Django")

    report = differences(a, b)

    assert report.ok
    assert report.skipped == 1


def test_an_allowed_difference_passes_and_is_listed():
    a = IMPORT_AND_LIST.format(**FIRST)
    b = IMPORT_AND_LIST.format(**dict(SHIFTED, bob_name="Robert"))
    seen = []

    def allow(difference):
        seen.append((difference.endpoint, difference.path))
        if difference.path == "$.racers[1].firstname":
            return "X1: a test divergence"
        return None

    report = differences(a, b, allow=allow)

    assert report.ok
    assert seen == [("query racer.list", "$.racers[1].firstname")]
    [allowed] = report.allowed
    assert allowed.allowed_by == "X1: a test divergence"
    assert "X1: a test divergence" in report.render()


def test_the_command_exits_non_zero_and_names_the_path(tmp_path, capsys):
    (tmp_path / "a.curl").write_text(run(IMPORT_AND_LIST.format(**FIRST)))
    (tmp_path / "b.curl").write_text(
        run(IMPORT_AND_LIST.format(**dict(SHIFTED, bob_name="Robert")))
    )
    (tmp_path / "c.curl").write_text(run(IMPORT_AND_LIST.format(**SHIFTED)))

    failed = td.main([str(tmp_path / "a.curl"), str(tmp_path / "b.curl")])
    out = capsys.readouterr().out
    passed = td.main([str(tmp_path / "a.curl"), str(tmp_path / "c.curl")])

    assert failed == 1
    assert "query racer.list" in out
    assert "action.php?query=racer.list" in out
    assert "$.racers[1].firstname" in out
    assert passed == 0
