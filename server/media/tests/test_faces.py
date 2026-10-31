"""The autocrop detector against PHP's (#35), replacing
`testing/test-face-detection.sh`.

PHP's baseline counts real misses. `detect_face()` reports a miss in a photo
larger than 320x240 as a box of zeros, not `false` (#74), and autocrop then
crops nothing. The count was taken on 2026-10-31 with the host's PHP 8.3.6 CLI
and GD (the reference server runs 8.4), by running
`testing/test-face-detection.php website/inc/facedetection.inc
testing/data/headshots` with `FACE_DETECTION_TIME_LIMIT=60`. That script
counts a zero-width box as no face, so its output lists the real misses:
`Cub-1612.jpg` and `head-A.jpg`, besides `no-photo.jpg`, which shows no face.
"""

from media import faces
from media.photos import _open_upright
from media.tests.conftest import HEADSHOTS

PHP_MISSES = {"Cub-1612.jpg", "head-A.jpg"}
NOT_A_FACE = "no-photo.jpg"


def detected():
    return {
        path.name: faces.detect(_open_upright(path)[0])
        for path in sorted(HEADSHOTS.glob("*.jpg"))
    }


def test_opencv_misses_no_more_headshots_than_php():
    found = detected()
    missed = sorted(name for name, face in found.items() if face is None)
    headshot_misses = [name for name in missed if name != NOT_A_FACE]

    assert len(found) == 102
    assert NOT_A_FACE in missed
    assert len(headshot_misses) <= len(PHP_MISSES), headshot_misses


def test_a_face_box_lies_inside_the_photo():
    for path in sorted(HEADSHOTS.glob("Cub-00*.jpg")):
        image = _open_upright(path)[0]
        face = faces.detect(image)
        assert face is not None, path.name
        assert 0 <= face.x and face.x + face.width <= image.width + 1
        assert 0 <= face.y and face.y + face.height <= image.height + 1
        assert face.width > image.width / 8
