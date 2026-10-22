import os
import time

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from eventapp.models import EventInfo, Heat, Racer, Result, TimerState
from spike import events_a


def _reply(request, t_start, rows_event, **extra):
    return JsonResponse({
        "event": EventInfo.objects.values_list("slug", flat=True).get(),
        "rows_event": rows_event,
        "pid": os.getpid(),
        "version": request.spike_version,
        "t_start": t_start,
        "t_end": time.time(),
        **extra,
    })


def _event_of(racer_name):
    return racer_name.split("-racer-")[0]


def kiosk_poll(request):
    t = time.time()
    heat = Heat.objects.filter(status="running").first() or Heat.objects.order_by("number").first()
    names = list(Result.objects.filter(heat=heat).values_list("racer__name", flat=True))
    return _reply(request, t, _event_of(names[0]), heat=heat.number, lanes=len(names))


def coord_poll(request):
    t = time.time()
    last = list(Result.objects.select_related("racer").order_by("-id")[:10])
    return _reply(request, t, _event_of(last[0].racer.name),
                  racers=Racer.objects.count(), heats=Heat.objects.count(), results=Result.objects.count())


@csrf_exempt
def timer_heartbeat(request):
    t = time.time()
    TimerState.objects.filter(pk=1).update(last_contact=t)
    return _reply(request, t, _event_of(Racer.objects.values_list("name", flat=True).first()))


@csrf_exempt
def switch(request):
    t = time.time()
    out = events_a.switch(request.GET["to"])
    return JsonResponse({**out, "pid": os.getpid(), "t_start": t})
