from legacy_api.xml import XmlWriter, php_escape


def test_escaping_matches_htmlspecialchars_with_ent_quotes():
    assert php_escape("'\"<>&") == "&#039;&quot;&lt;&gt;&amp;"
    assert php_escape("&amp;") == "&amp;amp;"
    assert php_escape("é") == "é"


def test_start_response_writes_each_field_as_an_attribute_in_order():
    writer = XmlWriter({"action": "timer-message", "message": "HELLO", "q": "a&b"})
    writer.start_response()
    writer.end_response()

    assert writer.document() == (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<action-response action="timer-message" message="HELLO" q="a&amp;b">'
        b"</action-response>\n"
    )


def test_a_query_response_has_no_attributes():
    writer = XmlWriter({})
    writer.not_authorized()

    assert writer.document() == (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<action-response><failure code="notauthorized">'
        b"Not authorized -- please see race coordinator</failure>"
        b"</action-response>\n"
    )


def test_take_action_reports_success_with_its_body():
    writer = XmlWriter({"action": "snapshot.put"})
    writer.take_action("<x/>")

    assert writer.document().endswith(
        b'<action-response action="snapshot.put"><success/><x/></action-response>\n'
    )


def test_a_nested_field_is_left_out_of_the_attributes():
    writer = XmlWriter({"action": "timer-message", "m": {"k": "v"}})
    writer.start_response()

    assert writer.document().endswith(b'<action-response action="timer-message">')


def test_a_handler_with_nothing_to_say_sends_only_the_prolog():
    assert XmlWriter({}).document() == b'<?xml version="1.0" encoding="UTF-8"?>\n'
