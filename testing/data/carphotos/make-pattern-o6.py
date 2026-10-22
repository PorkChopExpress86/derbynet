"""Writes pattern-o6.jpg, the EXIF orientation 6 fixture for issue #8.

Upright, the picture is 480x320 and split into four colored quarters: red,
green on top, blue, white below.  The stored pixels are that picture turned
90 degrees counterclockwise (320x480), with EXIF orientation 6, which tells a
viewer to turn it back.  That is how a phone stores a photo taken in portrait.

    python make-pattern-o6.py pattern-o6.jpg

With Pillow 11.0.0 the output is byte for byte the committed file.
"""
import sys

from PIL import Image, ImageOps

ORIENTATION = 0x0112
W, H = 480, 320

upright = Image.new("RGB", (W, H))
for x, y, color in [(0, 0, (220, 30, 30)), (W // 2, 0, (30, 200, 30)),
                    (0, H // 2, (30, 30, 220)), (W // 2, H // 2, (245, 245, 245))]:
    upright.paste(color, (x, y, x + W // 2, y + H // 2))

exif = Image.Exif()
exif[ORIENTATION] = 6
upright.transpose(Image.Transpose.ROTATE_90).save(sys.argv[1], "JPEG", quality=95, exif=exif.tobytes())

written = Image.open(sys.argv[1])
assert written.getexif()[ORIENTATION] == 6
assert ImageOps.exif_transpose(written).size == (W, H)
