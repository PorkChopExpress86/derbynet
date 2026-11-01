"""The transcript differ of the parity gate (#37), on hand-written runs.

`testing/common.sh` writes each exchange as a blank line, the request line, a
blank line and the response body, so the fixtures here are in that form.
"""

import re
import textwrap
from datetime import datetime
from pathlib import Path

from tools import transcript_diff as td

# When the hand-written runs ran: their timestamps are within a day of it.
NOW = datetime(2026, 11, 1, 1, 40)


def run(text: str) -> str:
    return textwrap.dedent(text)


def differences(a: str, b: str, allow=None) -> td.Report:
    return td.diff(run(a), run(b), allow=allow, now=NOW)


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
    assert report.diverged.a is td.MISSING


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
    # timer-message replies in XML (registry.format_of), so a JSON body is
    # not parsed as JSON: it is text.
    assert (found.a, found.b) == ("<xml>", "<text>")


def test_pages_are_not_compared():
    a = run("""
        kiosk.php?address=one

        <html><body>PHP</body></html>
        """)
    b = a.replace("PHP", "Django")

    report = differences(a, b)

    assert report.ok
    assert report.skipped == 1


CLASS_ADD = """
    post action.php action=class.add&rankid_{rank}=1&name=Agg

    {{"outcome": {{"summary": "success"}},
      "action": {{"action": "class.add", "rankid_{rank}": "1", "name": "Agg"}}}}

    post action.php action=award.edit&class_and_rank=1,{rank}

    {{"outcome": {{"summary": "success"}},
      "action": {{"action": "award.edit", "class_and_rank": "1,{rank}"}}}}
    """


def test_ids_in_an_echoed_request_are_numbered_as_in_the_request():
    report = differences(CLASS_ADD.format(rank=8), CLASS_ADD.format(rank=9))

    assert report.ok, report.render()


def test_echoed_number_fields_are_numbered_as_in_the_request():
    template = """
        post action.php action=roster.new&classid_{a}=1&classid_{b}=1

        {{"outcome": {{"summary": "success"}},
          "action": {{"action": "roster.new", "classid_{a}": 1, "classid_{b}": 1}}}}

        post action.php action=award.order&awardid_1={c}

        {{"outcome": {{"summary": "success"}},
          "action": {{"action": "award.order", "awardid_1": {c}}}}}
        """

    report = differences(
        template.format(a=1, b=2, c=4), template.format(a=97, b=98, c=17)
    )

    assert report.ok, report.render()


RESET = """
    post action.php action=racer.add&firstname=Ann&carno=1

    {{"outcome": {{"summary": "success"}}, "new-row": {{"racerid": {first}}}}}

    post action.php action=database.execute&script=schema

    {{"outcome": {{"summary": "success"}}}}

    post action.php action=racer.add&firstname=Bob&carno=2

    {{"outcome": {{"summary": "success"}}, "new-row": {{"racerid": {second}}}}}

    post action.php action=racer.delete&racer={second}

    {{"outcome": {{"summary": "success"}}}}
    """


def test_ids_are_numbered_afresh_after_a_database_reset():
    # PHP hands out racer 1 again after the reset; Django goes on to 2.
    report = differences(
        RESET.format(first=1, second=1), RESET.format(first=1, second=2)
    )

    assert report.ok, report.render()


def test_partition_order_names_ids_in_its_values():
    template = """
        post action.php action=partition.order&partitionid_1={a}&partitionid_2={b}

        {{"outcome": {{"summary": "success"}}}}

        action.php?query=poll&values=partitions

        {{"partitions": [{{"partitionid": {b}}}, {{"partitionid": {a}}}]}}
        """

    report = differences(template.format(a=3, b=2), template.format(a=48, b=47))

    assert report.ok, report.render()


def test_the_event_folder_and_mktemp_kiosk_names_are_masked():
    template = """
        action.php?query=settings.list&key=photo-directory

        {{"settings": {{"photo-directory": "/var/lib/derbynet/{folder}/racers"}}}}

        post action.php action=kiosk.assign&address=kiosk1-{name}&name=Main

        {{"outcome": {{"summary": "success"}}}}
        """
    php = template.format(folder="2026/testdb", name="JRN2XL")
    django = template.format(folder="events/testdb", name="8cB1wX")

    assert differences(php, django).ok
    # The data folder in front of the event's is compared.
    moved = django.replace("/var/lib/derbynet", "/srv/data")
    [found] = differences(php, moved).differences
    assert found.path == "$.settings.photo-directory"


def test_two_events_folders_still_differ():
    template = """
        post action.php action=settings.write&photo-dir=/var/lib/derbynet/{}/racers

        {{"outcome": {{"summary": "success"}}}}
        """

    same = differences(template.format("2026/testdb"), template.format("events/testdb"))
    other = differences(
        template.format("2026/testdb"), template.format("events/another-event")
    )

    assert same.ok
    assert other.diverged is not None


def test_a_long_value_is_cut_in_the_report():
    a = run("""
        action.php?query=racer.list

        {"text": "short"}
        """)
    b = a.replace("short", "x" * 1000)

    rendered = differences(a, b).render()

    assert "... (1002 characters)" in rendered
    assert "x" * 400 not in rendered


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

    def command(a, b, now=NOW):
        return td.main([str(tmp_path / a), str(tmp_path / b), "--now", now.isoformat()])

    failed = command("a.curl", "b.curl")
    out = capsys.readouterr().out
    passed = command("a.curl", "c.curl")
    # Years later the runs' timestamps are fixed dates, and they differ.
    later = command("a.curl", "c.curl", now=datetime(2030, 1, 1))

    assert failed == 1
    assert "query racer.list" in out
    assert "action.php?query=racer.list" in out
    assert "$.racers[1].firstname" in out
    assert passed == 0
    assert later == 1


# What `expect_photo_shape` (testing/test-photo-manipulations.sh) writes: a
# `photo <tail> <shape>` line with no blank lines around it, after a reply
# that may or may not end in a newline, and the tail ends in `$RANDOM`.
PHOTO_SHAPES = """
    post action.php action=photo.rotate&repo=car&image_name=pattern-o6.jpg&rotation=90

    {{"outcome": {{"summary": "success"}}, "cache-breaker": {breaker}}}photo car/file/original/pattern-o6.jpg/{r1} 320x480 6 RGBW
    photo car/file/200x200/pattern-o6.jpg/{r2} 200x133 none {quarters}

    photo head/file/original/ISO_12233-reschart.jpg/{r3}

    """  # noqa: E501


def test_photo_shapes_are_exchanges_of_their_own():
    exchanges = td.parse(
        run(PHOTO_SHAPES.format(breaker=1, r1=5, r2=6, r3=7, quarters="RGBW"))
    )

    assert [(e.request, e.body.strip()) for e in exchanges] == [
        (
            "post action.php action=photo.rotate&repo=car"
            "&image_name=pattern-o6.jpg&rotation=90",
            '{"outcome": {"summary": "success"}, "cache-breaker": 1}',
        ),
        ("photo car/file/original/pattern-o6.jpg/5", "320x480 6 RGBW"),
        ("photo car/file/200x200/pattern-o6.jpg/6", "200x133 none RGBW"),
        ("photo head/file/original/ISO_12233-reschart.jpg/7", ""),
    ]


def test_random_breakers_in_requests_are_masked_even_when_zero():
    a = PHOTO_SHAPES.format(breaker=1, r1=0, r2=31000, r3=0, quarters="RGBW")
    b = PHOTO_SHAPES.format(breaker=2, r1=17, r2=0, r3=9, quarters="RGBW")

    report = differences(a, b)

    assert report.ok, report.render()
    assert report.compared == 4


def test_a_changed_photo_shape_fails():
    a = PHOTO_SHAPES.format(breaker=1, r1=1, r2=2, r3=3, quarters="RGBW")
    b = PHOTO_SHAPES.format(breaker=1, r1=4, r2=5, r3=6, quarters="GWRB")

    [found] = differences(a, b).differences

    assert found.index == 3
    assert found.endpoint == "page photo.php"
    assert found.path == "text"
    assert (found.a, found.b) == ("200x133 none RGBW", "200x133 none GWRB")


def test_a_reply_cache_breaker_of_zero_against_non_zero_fails():
    a = PHOTO_SHAPES.format(breaker=0, r1=1, r2=2, r3=3, quarters="RGBW")
    b = PHOTO_SHAPES.format(breaker=7, r1=1, r2=2, r3=3, quarters="RGBW")

    [found] = differences(a, b).differences

    assert (found.path, found.a, found.b) == ("$.cache-breaker", 0, 7)


def test_bodies_without_a_trailing_newline_parse():
    text = (
        "\naction.php?query=poll\n\n"
        '{"a": 1}'
        "\npost action.php action=x.y&z=1\n\n"
        '<?xml version="1.0"?>\n<action-response/>'
        "\nlogin RaceCoordinator\n\n"
        '{"b": 2}'
    )

    exchanges = td.parse(text)

    assert [(e.request, e.body.strip()) for e in exchanges] == [
        ("action.php?query=poll", '{"a": 1}'),
        (
            "post action.php action=x.y&z=1",
            '<?xml version="1.0"?>\n<action-response/>',
        ),
        ("login RaceCoordinator", '{"b": 2}'),
    ]


KIOSKS = """
    action.php?query=poll.kiosk.all

    {{"kiosks": [{{"age": {age}, "last_contact": {contact}}}],
      "timecheck": {timecheck}, "age": {top_age},
      "replay": {{"last-contact": {replay}}}}}
    """


def kiosks(**values):
    defaults = {"age": 3, "contact": 9, "timecheck": '"t"', "top_age": 1, "replay": 5}
    return KIOSKS.format(**{**defaults, **values})


def test_a_volatile_value_that_changes_type_fails():
    report = differences(kiosks(), kiosks(age='"4"', timecheck="null"))

    assert [(d.path, d.a, d.b) for d in report.differences] == [
        ("$.kiosks[0].age", 3, "4"),
        ("$.timecheck", "t", None),
    ]


def test_volatile_values_of_one_type_match():
    changed = kiosks(age=8, contact=12, timecheck='"u"', replay=6)

    assert differences(kiosks(), changed).ok


def test_a_last_contact_of_zero_against_non_zero_fails():
    [found] = differences(kiosks(), kiosks(contact=0)).differences

    assert found.path == "$.kiosks[0].last_contact"


def test_age_outside_a_kiosk_entry_is_compared():
    [found] = differences(kiosks(), kiosks(top_age=2)).differences

    assert (found.path, found.a, found.b) == ("$.age", 1, 2)


AWARD = """
    action.php?query=award.list

    {{"awards": [{{"awardname": "Best", "presented": "{presented}",
                  "note": "given at {presented}"}}]}}
    """


def test_a_wrong_fixed_date_is_reported():
    a = AWARD.format(presented="2019-05-04 10:00:00")
    b = AWARD.format(presented="2019-05-05 10:00:00")

    found = differences(a, b).differences

    assert [d.path for d in found] == ["$.awards[0].presented", "$.awards[0].note"]


def test_timestamps_close_to_now_are_masked():
    a = AWARD.format(presented="2026-11-01 01:39:28")
    b = AWARD.format(presented="2026-10-31T20:39:29-0500")

    assert differences(a, b).ok


def test_an_xml_like_body_of_a_json_endpoint_is_text():
    # PHP's not_authorized_body() writes XML into a JSON reply (C21). The
    # endpoint's format, not the body, decides how it is read.
    a = run("""
        action.php?query=replay.message

        <failure code="notauthorized">Not authorized</failure>
        """)
    b = a.replace("Not authorized", "Refused")

    [found] = differences(a, b).differences

    assert found.path == "text"


ORDER = """
    post action.php action=class.add&name=Bears&rankid_{r1}=1&rankid_{r2}=1

    {{"classid": {c}}}

    post action.php action=rank.order&rankid_1={r2}&rankid_2={r1}

    {{"action": {{"action": "rank.order", "rankid_1": {r2}, "rankid_2": {r1}}},
      "outcome": {{"summary": "success"}}}}

    post action.php action=class.order&classid_1={c}

    {{"outcome": {{"summary": "success"}}}}
    """


def test_order_fields_name_a_position_and_an_id():
    # class.add's `rankid_<rankid>=1` names the rank in its key, rank.order's
    # `rankid_<position>=<rankid>` in its value.
    a = ORDER.format(r1=3, r2=8, c=2)

    assert differences(a, ORDER.format(r1=11, r2=16, c=12)).ok
    # The same ranks in the other order is another request.
    swapped = a.replace("rankid_1=8&rankid_2=3", "rankid_1=3&rankid_2=8")
    assert differences(a, swapped).diverged.index == 2


NORMALIZE_MJS = Path(__file__).resolve().parent / "golden/capture/normalize.mjs"


def test_every_golden_normalization_rule_is_accounted_for():
    text = NORMALIZE_MJS.read_text()
    rules = set(re.findall(r"^\s+id: '([\w-]+)',$", text, re.M))

    assert len(rules) == 9
    assert set(td.GOLDEN_RULES) == rules
