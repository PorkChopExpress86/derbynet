"""For the tests of closing a tab (#409): what reaches pytest-django's live
server, which runs in the test's own process, counted through Django's
request signals, and a send in the task after the harness stops a tab's
timers."""

import threading
from contextlib import contextmanager

from django.core.signals import request_finished, request_started


class AtTheServer:
    """The requests `match(environ)` picks that the server has started and
    finished."""

    def __init__(self, match):
        self.match = match
        self.started = 0
        self.finished = 0
        self._lock = threading.Lock()
        self._thread = threading.local()

    def start(self, sender, environ, **kwargs):
        self._thread.counted = self.match(environ)
        if self._thread.counted:
            with self._lock:
                self.started += 1

    def finish(self, sender, **kwargs):
        if getattr(self._thread, "counted", False):
            self._thread.counted = False
            with self._lock:
                self.finished += 1

    def after_close(self, page) -> tuple[int, int]:
        """The requests in hand now, and those started in the next 200 ms."""
        in_hand, started = self.started - self.finished, self.started
        page.wait_for_timeout(200)
        # One read after the wait: the point is that nothing more arrived.
        return in_hand, self.started - started


@contextmanager
def at_the_server(match):
    server = AtTheServer(match)
    request_started.connect(server.start)
    request_finished.connect(server.finish)
    try:
        yield server
    finally:
        request_started.disconnect(server.start)
        request_finished.disconnect(server.finish)


# Makes the first `clearTimeout` call, which the harness's STOP_TIMERS makes
# first in each frame, post a MessageChannel message that runs
# `window.afterTheStop()` in the next task: no timer STOP_TIMERS clears, as
# an RTCPeerConnection's callbacks or a reply's handler are none.
AFTER_THE_STOP = """() => {
  const clear = window.clearTimeout;
  window.clearTimeout = (id) => {
    window.clearTimeout = clear;
    const channel = new MessageChannel();
    channel.port1.onmessage = () => window.afterTheStop();
    channel.port2.postMessage(null);
    clear(id);
  };
}"""


def after_the_stop(tab, script: str):
    """Run `script`, a JavaScript function, in `tab` in the task after the
    harness stops its timers."""
    tab.evaluate(f"() => {{ window.afterTheStop = {script}; }}")
    tab.evaluate(AFTER_THE_STOP)
