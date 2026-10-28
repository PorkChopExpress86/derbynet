"""The generated images of fake racers (`website/inc/fake_images.inc`)."""

import os
import re
from pathlib import Path

FAKE_IMAGE = re.compile(r"fake_[0-9]*\.svg")


def remove_fake_images(directory) -> None:
    """Delete `fake_<digits>.svg` from a folder and the folders under it.

    A linked folder is not followed, so a setting that names one cannot send
    the search outside the photo folder.
    """
    if not directory:
        return
    try:
        entries = list(os.scandir(Path(directory)))
    except OSError:
        return
    for entry in entries:
        if entry.is_dir(follow_symlinks=False):
            remove_fake_images(entry.path)
        elif FAKE_IMAGE.fullmatch(entry.name):
            try:
                os.unlink(entry.path)
            except OSError:
                pass
