"""`query=stats` (`website/ajax/query.stats.inc`): counts of the event's racers,
heats and ballots."""

from django.db.models import Max
from django.utils import timezone

from awards.models import Award, BallotAward, Vote
from core.models import Event
from racing.models import HAS_RESULT, HeatLane
from roster.models import Racer


def counts(event: Event) -> dict:
    racers = Racer.objects.filter(event=event)
    lanes = HeatLane.objects.filter(event=event)
    raced = lanes.filter(HAS_RESULT, completed__isnull=False)
    ballot_award_ids = BallotAward.objects.filter(event=event).values("award_id")
    latest = raced.aggregate(latest=Max("completed"))["latest"]
    return {
        "racers": {
            "total": racers.count(),
            "passed": racers.filter(passed_inspection=True).count(),
            "racerphoto": racers.exclude(headshot="").count(),
            "carphoto": racers.exclude(car_photo="").count(),
        },
        "heats": {
            "scheduled": lanes.values("round_id", "heat").distinct().count(),
            "raced": raced.values("round_id", "heat").distinct().count(),
        },
        "latest_heat_run": None
        if latest is None
        else timezone.localtime(latest).strftime("%Y-%m-%d %H:%M:%S"),
        "voting": {
            "n_ballot_awards": BallotAward.objects.filter(event=event).count(),
            "n_ballot_unawarded": Award.objects.filter(
                event=event, pk__in=ballot_award_ids, racer__isnull=True
            ).count(),
            "nvotes": Vote.objects.filter(event=event).count(),
        },
    }
