"""Template tags for the #39 components.

`{% component "name" key=value %}body{% endcomponent %}` renders
`ui/components/<name>.html` with the keys and the rendered body as `body`, for
the components that wrap content (sheet, card list, card, filter bar,
sortable list, confirm sheet). Leaf components are plain includes.

`{% poll_trigger 2 %}` writes the `hx-trigger` of a section polled every 2 s,
which pauses while a sheet is open or a field of the section has focus.
"""

from django import template
from django.template.base import token_kwargs
from django.template.loader import render_to_string
from django.utils.html import format_html

register = template.Library()


class ComponentNode(template.Node):
    def __init__(self, name, kwargs, nodelist):
        self.name = name
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context):
        name = self.name.resolve(context)
        values = {key: value.resolve(context) for key, value in self.kwargs.items()}
        body = self.nodelist.render(context)
        component = context.template.engine.get_template(f"ui/components/{name}.html")
        with context.push(**values, body=body):
            return component.render(context)


@register.tag
def component(parser, token):
    bits = token.split_contents()
    if len(bits) < 2:
        raise template.TemplateSyntaxError("{% component %} needs a component name.")
    kwargs = token_kwargs(bits[2:], parser)
    if len(kwargs) != len(bits) - 2:
        raise template.TemplateSyntaxError(
            "{% component %} takes key=value arguments only."
        )
    nodelist = parser.parse(("endcomponent",))
    parser.delete_first_token()
    return ComponentNode(parser.compile_filter(bits[1]), kwargs, nodelist)


@register.filter
def racer_name(racer) -> str:
    """`{{ racer|racer_name }}`: the name as `ui.responses.racer_name`
    writes it."""
    from ui.responses import racer_name as name

    return name(racer.first_name, racer.last_name)


@register.filter(name="car")
def car_filter(number) -> str:
    """`{{ number|car }}`: a car number padded to three digits (D47)."""
    from ui.responses import car

    return car(number)


@register.simple_tag
def poll_trigger(seconds):
    return format_html('hx-trigger="every {}s [derbynet.pollable(this)]"', seconds)


@register.simple_tag(takes_context=True)
def kiosk_thumbnail(context, page, params="", address=None, name="", size="card"):
    """`{% kiosk_thumbnail page params address=... name=... size=... %}`: the
    thumbnail of the kiosk page string `page` with its parameters text, as
    the kiosk at `address` named `name` shows it (#237)."""
    from kiosks import pages
    from ui.kiosk_pages import snapshots

    html = snapshots.render(
        context["request"], page, params, address=address, name=name or ""
    )
    return render_to_string(
        "ui/kiosks/thumbnail.html",
        {"html": html, "brief": pages.page_for(page).name, "size": size},
    )
