import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.test import RequestFactory

from ui.responses import service_error

pytestmark = pytest.mark.django_db


def render(source, **context):
    return Template("{% load ui %}" + source).render(Context(context))


def test_the_gallery_is_off_on_a_race_day_server(client, event, settings):
    settings.DEBUG = False
    settings.ENABLE_TEST_ENDPOINTS = False

    assert client.get("/components/").status_code == 404
    assert client.post("/components/demo/ok/").status_code == 404


def test_the_gallery_renders_every_component(client, event, settings):
    settings.ENABLE_TEST_ENDPOINTS = True

    page = client.get("/components/").content.decode()

    for marker in [
        'role="search"',  # filter bar
        'role="list" aria-label="Racers"',  # card list
        'role="switch"',
        'role="dialog"',  # sheet
        "Delete 42 racers",  # confirm sheet
        'type="radio"',  # segmented control
        'type="search"',  # search field
        'data-state="ok"',  # status pill
        "uiStepper(",
        "uiSortable(",
        "Move up",
        'hx-trigger="every 2s [derbynet.pollable(this)]"',
    ]:
        assert marker in page, marker


@pytest.mark.parametrize(
    ("count", "label"), [(1, "Delete 1 racer"), (42, "Delete 42 racers")]
)
def test_the_confirm_sheet_names_the_count(count, label):
    html = render(
        '{% component "confirm_sheet" name="x" title="t" verb="Delete" count=count'
        ' noun="racer" hx_post="/x/" %}body{% endcomponent %}',
        count=count,
    )

    assert f">{label}</button>" in html


def test_a_plural_noun_can_be_given():
    html = render(
        '{% component "confirm_sheet" name="x" title="t" verb="Unschedule" count=3'
        ' noun="heat" noun_plural="heats of Lions" hx_post="/x/" %}{% endcomponent %}'
    )

    assert ">Unschedule 3 heats of Lions</button>" in html


def test_a_component_wraps_its_body():
    html = render('{% component "card" id=7 %}<b>Ava</b>{% endcomponent %}')

    assert 'data-id="7"' in html
    assert "<b>Ava</b>" in html


def test_a_component_takes_keywords_only():
    with pytest.raises(TemplateSyntaxError):
        render('{% component "card" 7 %}{% endcomponent %}')


def test_a_service_error_is_a_422_toast():
    response = service_error(RequestFactory().get("/"), "No such racer.")

    assert response.status_code == 422
    body = response.content.decode()
    assert 'role="alert"' in body
    assert "No such racer." in body


def test_a_toast_can_go_out_of_band():
    html = render(
        '{% include "ui/components/toast.html" with message="Passed." oob=True %}'
    )

    assert html.strip().startswith('<div hx-swap-oob="beforeend:#toasts">')


def test_the_toast_level_is_escaped_for_javascript():
    """M4: x-data is JavaScript, where an HTML-escaped quote is a quote."""
    html = render(
        '{% include "ui/components/toast.html" with message="m" level=level %}',
        level="x');alert(1);('",
    )

    assert "uiToast('x\\u0027)" in html
    assert "&#x27;" not in html.split("uiToast(", 1)[1].split(")", 1)[0]
