"""The Retrospective page (#49): `history.php`, behind CHECK_IN_RACERS as
in PHP (#15). `?debug` lists every logged action with its request, for
SET_UP only; anyone else gets the plain page."""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.permissions import Perm, allows
from ui.history import state
from ui.responses import active_event


@require_GET
def page(request):
    debug = "debug" in request.GET and allows(request, Perm.SET_UP)
    return render(
        request,
        "ui/history/page.html",
        {
            "retro": state.build(active_event(request), debug=debug),
            "can_debug": allows(request, Perm.SET_UP),
        },
    )
