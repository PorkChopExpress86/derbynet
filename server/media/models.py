from django.db import models


class PhotoRotation(models.Model):
    """How far a stored photo is turned, counterclockwise in degrees.

    PHP's `photo.rotate` rewrites the upload. #8's storage rule keeps the
    upload and stores the rotation as a field instead (#35). The rotation
    belongs to the file, not to a racer, so it is keyed by the photo folder
    and the file name, like the renders made from it: two events that share a
    folder share it, and a reset of the event's rows keeps it, as PHP's
    rewritten file does. A row with 0 degrees is never kept.
    """

    directory = models.CharField(max_length=500)
    name = models.CharField(max_length=255)
    degrees = models.FloatField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["directory", "name"], name="photorotation_one_per_file"
            ),
        ]

    def __str__(self):
        return f"{self.name} turned {self.degrees}"
