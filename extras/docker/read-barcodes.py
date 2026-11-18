#! /usr/bin/python3
"""Prints each barcode a USB scanner reads, one line each, forever (#59).

On a Pi install, photostand.sh reads its barcodes from the terminal the scanner
types into.  A container has no such terminal, so the photo stand image runs
this reader on the scanner's input device and pipes its lines into the
unchanged photostand.sh.

It picks the device as photostand.sh's find_barcode_scanner does: the first
name in BARCODE_SCANNER_DEVS found under /dev/input/by-id, else the first
entry there.  A device BARCODE_SCANNER_DEVS names it takes for itself
(EVIOCGRAB), so a scan types nowhere else on the Pi; the first entry it only
reads, since that may be the Pi's own keyboard.  When the device goes away, or
none is there, it looks again every 5 s, so a scanner plugged in late still
works.

The key tables are extras/scripts/lib/read_barcode.py's.  That reader unpacks
a 16-byte event, a 32-bit kernel's; this one reads the platform's own
`struct input_event` (two longs, then u16, u16, s32), 24 bytes on a 64-bit Pi.
"""

import fcntl
import os
import struct
import sys
import time

BY_ID = "/dev/input/by-id"
RETRY_SECONDS = 5

# struct input_event: struct timeval (two native longs), type, code, value.
EVENT = struct.Struct("@llHHi")
EV_KEY = 1
KEY_PRESSED = 1
KEY_AUTOREPEAT = 2
KEY_ENTER = 28
SHIFT_KEYS = {42, 54}
# _IOW('E', 0x90, int)
EVIOCGRAB = 0x40044590

KEYS = {
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    12: "-", 13: "=",
    16: "q", 17: "w", 18: "e", 19: "r", 20: "t", 21: "y", 22: "u", 23: "i", 24: "o", 25: "p",
    26: "[", 27: "]",
    30: "a", 31: "s", 32: "d", 33: "f", 34: "g", 35: "h", 36: "j", 37: "k", 38: "l", 39: ";",
    40: "'", 43: "\\",
    44: "z", 45: "x", 46: "c", 47: "v", 48: "b", 49: "n", 50: "m", 51: ",", 52: ".", 53: "/",
    57: " ",
}
SHIFTED = {
    2: "!", 3: "@", 4: "#", 5: "$", 6: "%", 7: "^", 8: "&", 9: "*", 10: "(", 11: ")",
    12: "_", 13: "+",
    16: "Q", 17: "W", 18: "E", 19: "R", 20: "T", 21: "Y", 22: "U", 23: "I", 24: "O", 25: "P",
    26: "{", 27: "}",
    30: "A", 31: "S", 32: "D", 33: "F", 34: "G", 35: "H", 36: "J", 37: "K", 38: "L", 39: ";",
    40: '"', 43: "|",
    44: "Z", 45: "X", 46: "C", 47: "V", 48: "B", 49: "N", 50: "M", 51: "<", 52: ">", 53: "?",
    57: " ",
}


def barcodes(stream):
    """Yields each line the key events in `stream` (a binary file) type, up to
    each Enter.  Ends when the stream does."""
    line = ""
    shift = False
    while True:
        raw = b""
        while len(raw) < EVENT.size:
            more = stream.read(EVENT.size - len(raw))
            if not more:
                return
            raw += more
        _sec, _usec, kind, code, value = EVENT.unpack(raw)
        if kind != EV_KEY:
            continue
        down = value in (KEY_PRESSED, KEY_AUTOREPEAT)
        if code in SHIFT_KEYS:
            shift = down
        elif not down:
            continue
        elif code == KEY_ENTER:
            yield line
            line = ""
        else:
            line += (SHIFTED if shift else KEYS).get(code, "")


def find_scanner(wanted, listing):
    """The device find_barcode_scanner picks, and whether to grab it: the
    first of `wanted` (BARCODE_SCANNER_DEVS's names) in `listing`, grabbed,
    else the first of `listing`, sorted as `ls` sorts it, not grabbed; None
    for an empty listing."""
    for name in wanted:
        if name in listing:
            return os.path.join(BY_ID, name), True
    for name in sorted(listing):
        return os.path.join(BY_ID, name), False
    return None


def main():
    wanted = os.environ.get("BARCODE_SCANNER_DEVS", "").split()
    while True:
        try:
            listing = os.listdir(BY_ID)
        except OSError:
            listing = []
        found = find_scanner(wanted, listing)
        if found is None:
            time.sleep(RETRY_SECONDS)
            continue
        device, grab = found
        try:
            with open(device, "rb", buffering=0) as stream:
                if grab:
                    try:
                        fcntl.ioctl(stream, EVIOCGRAB, 1)
                    except OSError as error:
                        print(f"read-barcodes: no grab of {device}: {error}", file=sys.stderr)
                for barcode in barcodes(stream):
                    print(barcode, flush=True)
        except OSError as error:
            print(f"read-barcodes: {device}: {error}", file=sys.stderr)
        time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    main()
