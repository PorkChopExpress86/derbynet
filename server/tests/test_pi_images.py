"""The Raspberry Pi images' barcode reader and checks (#59).

`extras/docker/read-barcodes.py` reads a USB scanner's key events and prints
each barcode, for the photo stand image's photostand.sh. On the Pi's arm
kernel a `struct input_event` is 24 bytes: two longs, then the type, the code
and the value. extras/scripts/lib/read_barcode.py unpacks a shorter one
(`2IHHI`). These tests pack the events by hand in the little-endian layout of
two longs, not through the reader's own struct, so a reader that unpacked any
other layout fails them.

`extras/docker/ci/checks.sh` runs against the images in
`.github/workflows/pi-images.yml`; its header names each check, and the
workflow runs each one, as `test_docker_checks.py` holds for the server
image's checks.
"""

import ast
import importlib.util
import io
import re
import struct
from pathlib import Path

import pytest

from tests.workflow_steps import steps

ROOT = Path(__file__).resolve().parents[2]
READER = ROOT / "extras" / "docker" / "read-barcodes.py"
PHP_READER = ROOT / "extras" / "scripts" / "lib" / "read_barcode.py"
CHECKS = ROOT / "extras" / "docker" / "ci" / "checks.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "pi-images.yml"
BY_ID = "/dev/input/by-id"

LP64_EVENT = struct.Struct("<qqHHi")
EV_SYN, EV_KEY, EV_REL, EV_MSC = 0, 1, 2, 4
SHIFT, RIGHT_SHIFT, ENTER = 42, 54, 28


def load_reader():
    spec = importlib.util.spec_from_file_location("read_barcodes", READER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = load_reader()


def event(kind, code, value, sec=1_700_000_000, usec=123_456):
    return LP64_EVENT.pack(sec, usec, kind, code, value)


def press(code):
    """A key going down, with the scan code (EV_MSC) and the sync
    (EV_SYN) a keyboard sends around it, then going up."""
    return b"".join(
        [
            event(EV_MSC, 4, 0x70000 + code),
            event(EV_KEY, code, 1),
            event(EV_SYN, 0, 0),
            event(EV_KEY, code, 0),
            event(EV_SYN, 0, 0),
        ]
    )


def shifted(code):
    return event(EV_KEY, SHIFT, 1) + press(code) + event(EV_KEY, SHIFT, 0)


# PWD101 as a scanner types it: shift held for P, W and D.
PWD101 = (
    shifted(25)
    + shifted(17)
    + shifted(32)
    + press(2)
    + press(11)
    + press(2)
    + press(ENTER)
)


class Trickle(io.RawIOBase):
    """A device that hands back at most `step` bytes a read, as a pipe can."""

    def __init__(self, data, step):
        self.data, self.step = data, step

    def readable(self):
        return True

    def read(self, n=-1):
        chunk, self.data = (
            self.data[: min(n, self.step)],
            self.data[min(n, self.step) :],
        )
        return chunk


def test_the_event_is_24_bytes_as_on_the_pi():
    assert LP64_EVENT.size == 24
    assert reader.EVENT.size == 24
    assert struct.calcsize("=2IHHI") < 24


def test_a_scan_reads_back_as_its_barcode():
    assert list(reader.barcodes(io.BytesIO(PWD101))) == ["PWD101"]


def test_two_scans_read_back_in_order():
    data = PWD101 + shifted(25) + shifted(17) + shifted(32) + press(3) + press(ENTER)
    assert list(reader.barcodes(io.BytesIO(data))) == ["PWD101", "PWD2"]


@pytest.mark.parametrize("step", [1, 5, 23, 25])
def test_a_scan_split_across_reads_reads_back_whole(step):
    assert list(reader.barcodes(Trickle(PWD101, step))) == ["PWD101"]


def test_a_held_key_repeats():
    data = (
        event(EV_KEY, 2, 1) + event(EV_KEY, 2, 2) + event(EV_KEY, 2, 0) + press(ENTER)
    )
    assert list(reader.barcodes(io.BytesIO(data))) == ["11"]


def test_the_right_shift_key_shifts_too():
    data = event(EV_KEY, RIGHT_SHIFT, 1) + press(25) + event(EV_KEY, RIGHT_SHIFT, 0)
    assert list(reader.barcodes(io.BytesIO(data + press(ENTER)))) == ["P"]


def test_an_event_that_is_no_key_types_nothing():
    """A relative move whose code and value read as key 1 going down."""
    data = event(EV_REL, 2, 1) + press(3) + press(ENTER)
    assert list(reader.barcodes(io.BytesIO(data))) == ["2"]


def test_a_key_after_shift_goes_up_is_lower_case():
    data = shifted(25) + press(25) + press(ENTER)
    assert list(reader.barcodes(io.BytesIO(data))) == ["Pp"]


def test_a_cut_off_event_ends_the_stream_without_a_barcode():
    data = press(2) + event(EV_KEY, ENTER, 1)[:-1]
    assert list(reader.barcodes(io.BytesIO(data))) == []


def test_the_key_tables_are_the_pi_install_reader_s():
    tree = ast.parse(PHP_READER.read_text())
    tables = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("keys", "shift_keys")
    }
    assert reader.KEYS == tables["keys"]
    assert reader.SHIFTED == tables["shift_keys"]


@pytest.mark.parametrize(
    ("wanted", "listing", "expected"),
    [
        ([], [], None),
        ([], ["usb-b-kbd", "usb-a-kbd"], (f"{BY_ID}/usb-a-kbd", False)),
        (["usb-b-kbd"], ["usb-a-kbd", "usb-b-kbd"], (f"{BY_ID}/usb-b-kbd", True)),
        (
            ["usb-gone", "usb-b-kbd"],
            ["usb-a-kbd", "usb-b-kbd"],
            (f"{BY_ID}/usb-b-kbd", True),
        ),
        (["usb-gone"], ["usb-a-kbd"], (f"{BY_ID}/usb-a-kbd", False)),
    ],
)
def test_the_scanner_is_picked_as_find_barcode_scanner_picks_it(
    wanted, listing, expected
):
    """Only a device BARCODE_SCANNER_DEVS names is grabbed: the first entry
    may be the Pi's keyboard."""
    assert reader.find_scanner(wanted, listing) == expected


ARM = re.compile(r"^ +([a-z][a-z-]*)\) (check_\w+) ;;$", re.M)
HEADER = re.compile(r"^# Checks:\n((?:#[^\n]*\n)*?)#\n", re.M)
HEADER_NAME = re.compile(r"^#   ([a-z][a-z-]*)(?: |$)", re.M)
CALL = re.compile(r"^(?:\S+=\S+ +)*extras/docker/ci/checks\.sh((?: [a-z][a-z-]*)*) *$")


def test_the_header_names_each_check():
    text = CHECKS.read_text()
    header = HEADER.search(text)
    assert header
    assert sorted(HEADER_NAME.findall(header.group(1))) == sorted(
        dict(ARM.findall(text))
    )


def test_pi_images_yml_runs_each_check():
    """A live step runs each check by name: a bare call would hide which."""
    named = set(dict(ARM.findall(CHECKS.read_text())))
    calls = [
        match.group(1).split()
        for step in steps(WORKFLOW.read_text())
        if step.runs
        for line in step.script.splitlines()
        if (match := CALL.match(line.strip()))
    ]
    assert calls and all(calls)
    assert {name for call in calls for name in call} == named


def read_once(capsys, wanted, by_id):
    reader.read_once(wanted, str(by_id))
    return capsys.readouterr()


def test_read_once_with_no_device_prints_nothing(tmp_path, capsys):
    out = read_once(capsys, [], tmp_path)
    assert (out.out, out.err) == ("", "")


def test_read_once_with_no_folder_prints_nothing(tmp_path, capsys):
    out = read_once(capsys, [], tmp_path / "missing")
    assert (out.out, out.err) == ("", "")


def test_read_once_reads_the_first_device_without_a_grab(tmp_path, capsys):
    (tmp_path / "usb-a-kbd").write_bytes(PWD101)
    out = read_once(capsys, [], tmp_path)
    assert (out.out, out.err) == ("PWD101\n", "")


def test_read_once_grabs_a_named_device(tmp_path, capsys):
    """A plain file refuses the grab, and the refusal names the device: the
    reader tries to grab a device BARCODE_SCANNER_DEVS names."""
    (tmp_path / "usb-a-kbd").write_bytes(b"")
    (tmp_path / "usb-b-kbd").write_bytes(PWD101)
    out = read_once(capsys, ["usb-b-kbd"], tmp_path)
    assert out.out == "PWD101\n"
    assert out.err.startswith(f"read-barcodes: no grab of {tmp_path}/usb-b-kbd: ")


def test_read_once_reports_a_device_it_cannot_open(tmp_path, capsys):
    (tmp_path / "usb-a-kbd").mkdir()
    out = read_once(capsys, [], tmp_path)
    assert out.out == ""
    assert out.err.startswith(f"read-barcodes: {tmp_path}/usb-a-kbd: ")


class Stop(Exception):
    pass


def test_main_reads_the_named_scanners_then_waits_and_reads_again(monkeypatch):
    """main() passes BARCODE_SCANNER_DEVS's names to each pass, and waits
    RETRY_SECONDS between passes; the second wait stops the test."""
    passes, waits = [], []

    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise Stop

    monkeypatch.setenv("BARCODE_SCANNER_DEVS", "usb-b-kbd usb-c-kbd")
    monkeypatch.setattr(reader, "read_once", passes.append)
    monkeypatch.setattr(reader.time, "sleep", wait)
    with pytest.raises(Stop):
        reader.main()
    assert passes == [["usb-b-kbd", "usb-c-kbd"]] * 2
    assert waits == [reader.RETRY_SECONDS] * 2


def test_the_pi_images_build_on_the_server_image_s_base():
    """The server image and both Pi images name the same Ubuntu digest, so a
    base update moves them together."""
    dockerfiles = [
        ROOT / "server" / "docker" / "Dockerfile",
        ROOT / "extras" / "docker" / "timer.Dockerfile",
        ROOT / "extras" / "docker" / "photostand.Dockerfile",
    ]
    bases = {
        line
        for path in dockerfiles
        for line in path.read_text().splitlines()
        if line.startswith("ARG UBUNTU=")
    }
    assert len(bases) == 1, bases


def test_push_and_pull_request_watch_the_same_paths():
    text = WORKFLOW.read_text()

    def paths(event):
        block = text.split(f"\n  {event}:\n", 1)[1].split("\n  workflow_dispatch:", 1)[
            0
        ]
        block = block.split("\n  pull_request:", 1)[0]
        return [
            line.strip() for line in block.splitlines() if line.startswith("      - ")
        ]

    assert paths("push")
    assert paths("push") == paths("pull_request")
