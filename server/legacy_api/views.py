"""`/action.php`: the dispatcher of `website/action.php`, with its envelope (#17).

Hardware clients post without a CSRF token, so the view is exempt. Every
outcome answers 200, never an HTML error page, and nothing here redirects
except the setup redirect PHP sends when no event is open.
"""

import logging

from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.permissions import allows
from core.models import ActionLog, SiteState
from legacy_api.encoding import encode_php_json
from legacy_api.forms import parse_php_form
from legacy_api.registry import (
    REFUSALS,
    REGISTRY,
    Format,
    LegacyContext,
    ReadOnlySession,
    format_of,
    is_logged,
    needs_event,
)
from legacy_api.xml import PROLOG, XmlWriter

logger = logging.getLogger(__name__)

NOT_A_PAGE = (
    b"<!DOCTYPE html><html><head><title>Not a Page</title></head>"
    b"<body><h1>This is not a page.</h1></body></html>"
)
CONTENT_TYPES = {
    Format.JSON: "application/json; charset=utf-8",
    Format.XML: "text/xml; charset=utf-8",
}
IN_PROGRESS = {
    "summary": "in-progress",
    "code": "in-progress",
    "description": "No outcome defined.",
}
SERVER_ERROR = ("server-error", "Unexpected server error")


def _response(body: bytes, content_type: str, status: int = 200) -> HttpResponse:
    response = HttpResponse(body, content_type=content_type, status=status)
    # PHP's session_start() sends this with every response.
    response["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


def _active_event():
    state = SiteState.objects.select_related("active_event").filter(pk=1).first()
    return state.active_event if state else None


def _masked(fields: dict) -> dict:
    return {
        name: "..." if name == "password" else value for name, value in fields.items()
    }


def _record_action(event, echo: dict) -> None:
    """PHP's record_action() swallows a PDOException, so a log row the
    database refuses never fails the request. The savepoint keeps the failed
    insert from aborting a transaction the request runs in."""
    try:
        with transaction.atomic():
            ActionLog.objects.create(event=event, request=echo)
    except DatabaseError:
        logger.warning("the action log refused %s", echo.get("action"), exc_info=True)


def _run(ctx: LegacyContext, endpoint, fmt: Format) -> bytes:
    """Run the handler and encode what it wrote, or a server-error failure."""
    try:
        endpoint.handler(ctx)
        return ctx.xml.document() if fmt is Format.XML else encode_php_json(ctx.out)
    except Exception:
        logger.exception("legacy %s %s raised", endpoint.kind, endpoint.name)
    if fmt is Format.XML:
        ctx.xml.failure_response(*SERVER_ERROR)
        return ctx.xml.document()
    ctx.out = {key: value for key, value in ctx.out.items() if key == "action"}
    ctx.failure(*SERVER_ERROR)
    return encode_php_json(ctx.out)


@csrf_exempt
def dispatch(request):
    post = parse_php_form(request.POST)
    get = parse_php_form(request.GET)
    if not post and not get:
        return _response(NOT_A_PAGE, "text/html; charset=UTF-8")

    kind = "action" if post else "query"
    args = post or get
    name = args.get(kind)
    # PHP answers a 500 for an array name, such as query[]=poll.
    name = name if isinstance(name, str) else ""
    fmt = format_of(name)

    event = _active_event()
    if event is None and needs_event(name):
        body = PROLOG.encode() if fmt is Format.XML else b""
        response = _response(body, CONTENT_TYPES[fmt], status=302)
        response["Location"] = "setup.php"
        return response

    endpoint = REGISTRY.get(kind, name)
    session = request.session
    if not (endpoint and endpoint.session_write):
        session = ReadOnlySession(session, name)
    ctx = LegacyContext(args, event, session, XmlWriter(_masked(post)))
    if kind == "action":
        echo = _masked(args)
        ctx.put("action", echo)
        ctx.put("outcome", dict(IN_PROGRESS))
        if event is not None and is_logged(name, args):
            _record_action(event, echo)

    if endpoint is None:
        if fmt is Format.XML:
            ctx.xml.failure_response("unrecognized", f"Unrecognized {kind}: {name}")
        else:
            ctx.failure("unrecognized", f"Unrecognized {kind}: {name}")
    elif not allows(request.session, endpoint.permission):
        if fmt is Format.XML:
            ctx.xml.not_authorized()
        else:
            ctx.failure(endpoint.failure_code, REFUSALS[endpoint.failure_code])
    else:
        return _response(_run(ctx, endpoint, fmt), CONTENT_TYPES[fmt])

    body = ctx.xml.document() if fmt is Format.XML else encode_php_json(ctx.out)
    return _response(body, CONTENT_TYPES[fmt])
