"""Face detection for autocrop (#35), replacing `website/inc/facedetection.inc`.

OpenCV's `haarcascade_frontalface_alt2` cascade runs on the upright photo,
scaled down so its longer side is 320 pixels, as PHP's detector scales to
320x240. A miss is reported as a miss, `None`, never as PHP's box of zeros
(#74). `media/tests/test_faces.py` holds the detector to PHP's miss count over
`testing/data/headshots`.

`DERBYNET_FACEDETECT` names an external detector instead, as PHP's
`$_SERVER['DERBYNET_FACEDETECT']` does. It runs with an argument list and no
shell, and its last line of output is `x y width height`.
"""

import logging
import os
import subprocess
import threading
from dataclasses import dataclass

import cv2
import numpy
from PIL import Image

logger = logging.getLogger(__name__)

CASCADE = cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml"
DETECTION_SIZE = 320
SCALE_FACTOR = 1.05
MIN_NEIGHBORS = 3
HOOK_TIMEOUT_SECONDS = 60

_cascade = None
# A CascadeClassifier is not safe to share between threads.
_cascade_lock = threading.Lock()


@dataclass(frozen=True)
class Face:
    x: float
    y: float
    width: float
    height: float


def detect(image: Image.Image) -> Face | None:
    """The largest face in an upright image, in its pixels, or None."""
    global _cascade
    scale = 1.0
    gray = image.convert("L")
    if max(gray.size) > DETECTION_SIZE:
        scale = DETECTION_SIZE / max(gray.size)
        gray = gray.resize(
            (max(1, round(gray.width * scale)), max(1, round(gray.height * scale))),
            Image.Resampling.LANCZOS,
        )
    pixels = cv2.equalizeHist(numpy.asarray(gray))
    smallest = max(20, min(pixels.shape) // 8)
    with _cascade_lock:
        if _cascade is None:
            _cascade = cv2.CascadeClassifier(CASCADE)
        faces = _cascade.detectMultiScale(
            pixels,
            scaleFactor=SCALE_FACTOR,
            minNeighbors=MIN_NEIGHBORS,
            minSize=(smallest, smallest),
        )
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces.tolist(), key=lambda f: f[2] * f[3])
    return Face(x / scale, y / scale, w / scale, h / scale)


def hook_is_usable(command: str) -> bool:
    """PHP's `is_executable()` test: otherwise the built-in detector runs."""
    return bool(command) and os.path.isfile(command) and os.access(command, os.X_OK)


def detect_with_hook(command: str, path: str) -> Face | None:
    """Run the external detector on `path`. Output that is not four numbers
    on the last line is a miss, as in PHP."""
    completed = subprocess.run(
        [command, path],
        capture_output=True,
        text=True,
        timeout=HOOK_TIMEOUT_SECONDS,
        check=False,
    )
    lines = completed.stdout.rstrip().splitlines()
    parts = lines[-1].split(" ") if lines else []
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (float(part) for part in parts)
    except ValueError:
        return None
    return Face(x, y, w, h)
