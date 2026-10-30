"""`madlib_string()` against the names PHP gave twenty addresses, as the
capture `http/kiosks/poll-kiosk.http` (022) records them."""

import pytest

from kiosks.madlib import madlib

PHP_NAMES = {
    "00:1A:2B:3C:4D:5E": "brave turkey",
    "10.0.0.1": "loose ghost",
    "10.0.0.2": "shallow coast",
    "10.0.0.254": "wide-eyed geese",
    "127.0.0.1": "immense vegetable",
    "172.16.5.5": "substantial east",
    "192.168.1.10": "mushy badge",
    "192.168.1.11": "elegant riddle",
    "192.168.1.12": "bewildered poison",
    "192.168.1.13": "bumpy patch",
    "192.168.1.14": "weak smoke",
    "192.168.1.15": "strong governor",
    "192.168.1.16": "proud south",
    "192.168.1.17": "wide-eyed badge",
    "192.168.1.18": "cold beggar",
    "192.168.1.19": "plain seashore",
    "AA:BB:CC:DD:EE:FF": "green arithmetic",
    "fe80::1": "rotten frame",
    "kiosk-with-a-name": "lazy poison",
    "localhost": "delicious west",
}


@pytest.mark.parametrize(("address", "name"), PHP_NAMES.items())
def test_a_kiosk_keeps_the_name_php_gave_its_address(address, name):
    assert madlib(address) == name
