from django.db import models


class EventInfo(models.Model):
    """Singleton row inside each event database; the identity a response reports."""

    slug = models.CharField(max_length=64)


class Racer(models.Model):
    name = models.CharField(max_length=200)
    car_number = models.IntegerField()
    note = models.TextField(default="")


class Heat(models.Model):
    number = models.IntegerField()
    status = models.CharField(max_length=16, default="scheduled")


class Result(models.Model):
    heat = models.ForeignKey(Heat, on_delete=models.CASCADE)
    racer = models.ForeignKey(Racer, on_delete=models.CASCADE)
    lane = models.IntegerField()
    time = models.FloatField(null=True)

    class Meta:
        indexes = [models.Index(fields=["heat", "lane"], name="result_heat_lane")]


class Award(models.Model):
    name = models.CharField(max_length=100)
    racer = models.ForeignKey(Racer, null=True, on_delete=models.SET_NULL)


class TimerState(models.Model):
    last_contact = models.FloatField(default=0)
