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


@register.simple_tag
def poll_trigger(seconds):
    return format_html('hx-trigger="every {}s [derbynet.pollable(this)]"', seconds)
