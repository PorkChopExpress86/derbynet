from django.http import QueryDict

from legacy_api.forms import parse_php_form


def parse(query):
    return parse_php_form(QueryDict(query))


def test_a_bracketed_name_is_a_nested_array():
    assert parse("message[recipient]=AA&message[key]=v") == {
        "message": {"recipient": "AA", "key": "v"}
    }


def test_deeper_nesting_builds_arrays_inside_arrays():
    assert parse("a[b][c]=1") == {"a": {"b": {"c": "1"}}}


def test_empty_brackets_append_to_a_list():
    assert parse("list[]=1&list[]=2") == {"list": ["1", "2"]}


def test_integer_indexes_from_zero_in_order_are_a_list():
    assert parse("a[0]=x&a[1]=y") == {"a": ["x", "y"]}
    assert parse("a[1]=x") == {"a": {1: "x"}}
    assert parse("a[01]=x") == {"a": {"01": "x"}}


def test_a_dot_or_space_in_a_top_level_name_becomes_an_underscore():
    assert parse("a.b=1&c+d=2") == {"a_b": "1", "c_d": "2"}


def test_a_dot_inside_brackets_is_kept():
    assert parse("a[b.c]=1") == {"a": {"b.c": "1"}}


def test_an_unclosed_bracket_is_part_of_the_name():
    assert parse("a[b=1") == {"a_b": "1"}


def test_text_after_the_last_bracket_is_ignored():
    assert parse("a[b]c=1") == {"a": {"b": "1"}}


def test_an_unclosed_bracket_after_an_index_is_ignored():
    assert parse("a[b][c=1") == {"a": {"b": "1"}}


def test_empty_brackets_take_the_index_after_the_largest():
    assert parse("a[-5]=x&a[]=y") == {"a": {-5: "x", -4: "y"}}
    assert parse("a[3]=x&a[]=y&a[1]=z&a[]=w") == {"a": {3: "x", 4: "y", 1: "z", 5: "w"}}


def test_a_bare_key_is_present_with_an_empty_value():
    assert parse("message=HEARTBEAT&unhealthy") == {
        "message": "HEARTBEAT",
        "unhealthy": "",
    }


def test_a_repeated_key_keeps_its_last_value_in_its_first_place():
    assert list(parse("x=1&y=2&x=3").items()) == [("x", "3"), ("y", "2")]


def test_a_scalar_is_replaced_by_an_array_and_back():
    assert parse("a=1&a[b]=2") == {"a": {"b": "2"}}
    assert parse("a[b]=2&a=1") == {"a": "1"}


def test_a_name_with_no_base_is_dropped():
    assert parse("[a]=1&=2") == {}


def test_values_stay_strings():
    assert parse("n=007&e=1e3") == {"n": "007", "e": "1e3"}
