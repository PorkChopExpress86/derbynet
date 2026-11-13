"""The vote page (#54): `vote.php` and `vote.js` rebuilt for a phone, over
the legacy ballot endpoints.

The page renders every judged award and every racer who can be voted for.
In the browser it asks `action.php?query=ballot.get` for the voter's ballot
and posts each change to `action=vote.cast`, so the page and any other
client share one code path. It polls `/ballot/status` every 5 s, which
answers `{"balloting": "open"}` or `{"balloting": "closed"}` and nothing
else, and swaps its state in place (D77). `vote.js` polls `settings.list`,
which any voter can ask for `ballot_password` (#64 S2), and reloads the
page (`website/js/vote.js:203` to `:220`).

A voter gets in with a racer's voting token (#206, D86), where `vote.php`
asks for the ballot password: the QR code on the check-in page carries the
vote link, `/vote/code/<token>`, which puts the token in the session and
redirects here, so the token leaves the address bar. With no valid token
the page asks for a code.
"""

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from awards import balloting
from ui.responses import active_event
from ui.vote import state


@require_GET
def page(request):
    order = state.order_of(request.GET.get("order"))
    found = state.build(
        active_event(request), order, request.session.get(balloting.SESSION_TOKEN)
    )
    return render(
        request,
        "ui/vote/page.html",
        {
            "page": found,
            "config": {
                **found.config(),
                "statusUrl": reverse("ballot-status"),
                "actionUrl": reverse("action-php"),
                "pollSeconds": state.POLL_SECONDS,
            },
            "sort_options": (
                ("car", "Car number"),
                ("class", found.group_label),
                ("name", "Name"),
            ),
            "page_title": "Vote",
        },
    )


@require_GET
def token_link(request, token):
    """The vote link a racer's QR code carries (#206, D86). A token of a
    racer of the active event goes into the session, and the vote page
    opens. Any other, malformed, unknown, another event's or a deleted
    racer's, answers the same 404 page and leaves the session with no
    token, so the phone does not go on voting with an earlier code."""
    event = active_event(request)
    text = token.removesuffix("/")
    racer = balloting.token_racer(event, text)
    if racer is not None:
        request.session[balloting.SESSION_TOKEN] = str(racer.voting_token)
        return redirect("vote")
    request.session.pop(balloting.SESSION_TOKEN, None)
    return render(request, "ui/vote/bad_code.html", {"page_title": "Vote"}, status=404)


@require_GET
def status(request):
    """Whether balloting is open, for anyone. With no event open, it is
    closed."""
    try:
        event = active_event(request)
    except Http404:
        return JsonResponse({"balloting": balloting.CLOSED})
    is_open = balloting.is_open(event)
    return JsonResponse({"balloting": balloting.OPEN if is_open else balloting.CLOSED})
