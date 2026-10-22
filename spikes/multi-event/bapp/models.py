from django.db import models


class Event(models.Model):
    slug = models.CharField(max_length=64, unique=True)


class SiteState(models.Model):
    active_event = models.ForeignKey(Event, on_delete=models.PROTECT)


class Racer(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    car_number = models.IntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "car_number"], name="racer_car_per_event")]


class Heat(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    number = models.IntegerField()
    status = models.CharField(max_length=16, default="scheduled")


class Result(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    heat = models.ForeignKey(Heat, on_delete=models.CASCADE)
    racer = models.ForeignKey(Racer, on_delete=models.CASCADE)
    lane = models.IntegerField()
    time = models.FloatField(null=True)


class TimerState(models.Model):
    event = models.OneToOneField(Event, on_delete=models.CASCADE)
    last_contact = models.FloatField(default=0)
