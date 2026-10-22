import os
import time

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from bapp.models import Event, Heat, Racer, Result, SiteState, TimerState


def _reply(request, t_start, rows_event, **extra):
    return JsonResponse({
        "event": request.event.slug,
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
    heats = Heat.objects.all() if settings.FAULT == "leak" else Heat.objects.filter(event=request.event)
    heat = heats.filter(status="running").first() or heats.order_by("number").first()
    names = list(Result.objects.filter(heat=heat).values_list("racer__name", flat=True))
    return _reply(request, t, _event_of(names[0]), heat=heat.number, lanes=len(names))


def coord_poll(request):
    t = time.time()
    ev = request.event
    last = list(Result.objects.filter(event=ev).select_related("racer").order_by("-id")[:10])
    return _reply(request, t, _event_of(last[0].racer.name),
                  racers=Racer.objects.filter(event=ev).count(),
                  heats=Heat.objects.filter(event=ev).count(),
                  results=Result.objects.filter(event=ev).count())


@csrf_exempt
def timer_heartbeat(request):
    t = time.time()
    TimerState.objects.filter(event=request.event).update(last_contact=t)
    return _reply(request, t, _event_of(Racer.objects.filter(event=request.event).values_list("name", flat=True).first()))


@csrf_exempt
def switch(request):
    t = time.time()
    target = Event.objects.get(slug=request.GET["to"])
    with transaction.atomic():
        previous = SiteState.objects.select_related("active_event").select_for_update().get(pk=1).active_event.slug
        SiteState.objects.filter(pk=1).update(active_event=target)
    return JsonResponse({"from": previous, "to": target.slug, "commit_ts": time.time(), "migrate_s": 0.0,
                         "pid": os.getpid(), "t_start": t})
