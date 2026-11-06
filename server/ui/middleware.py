"""`EventReadOnlyToast`: a write service's `EventReadOnly` refusal, raised
anywhere under a `ui` view, as an error toast with HTTP 422 (#212).

A past event refuses every change (#9, Past events). The refusal is the
write service's, under its lock, so no view checks the event first and
acts on what it read (`plan/AGENT-BRIEF.md`). The service's transaction
has rolled back by the time the exception reaches here.
"""

from core.services import EventReadOnly
from ui.responses import service_error


class EventReadOnlyToast:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, EventReadOnly):
            return service_error(request, exception.description)
        return None
