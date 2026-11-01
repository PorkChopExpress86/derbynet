"""The value of a key one reply lacks, for the parity gate (#37).

`tools/transcript_diff.py` reports a key only one server's reply has with
`MISSING` on the other side, and an entry of `PARITY_PATHS` in
`legacy_api/registry.py` names it to allow exactly that. It lives here, with
no imports, so both can use it without loading Django.
"""


class _Missing:
    """No value: a key, an element or an exchange one run lacks."""

    def __repr__(self):
        return "(missing)"

    __str__ = __repr__


MISSING = _Missing()
