"""The XML documents of `website/inc/action-helpers.inc`, byte for byte.

`derby-timer.jar` and the timer goldens compare attribute order and
whitespace, so this writes strings, not an ElementTree (#17).
"""

PROLOG = '<?xml version="1.0" encoding="UTF-8"?>\n'

_ESCAPES = str.maketrans(
    {"&": "&amp;", '"': "&quot;", "'": "&#039;", "<": "&lt;", ">": "&gt;"}
)


def php_escape(text: str) -> str:
    """`htmlspecialchars($text, ENT_QUOTES, 'UTF-8')`. Python's `html.escape`
    writes `&#x27;` for an apostrophe, where PHP writes `&#039;`."""
    return text.translate(_ESCAPES)


class XmlWriter:
    """Collects one XML response after the prolog, as PHP echoes it.

    `fields` are the POST fields that `start_response()` writes as attributes
    of `<action-response>`. A query has none.
    """

    def __init__(self, fields: dict):
        self._fields = fields
        self._parts: list[str] = []

    def write(self, xml: str) -> None:
        self._parts.append(xml)

    def start_response(self) -> None:
        # PHP fails on an array field here. A nested form field is left out.
        attributes = "".join(
            f' {name}="{php_escape(value)}"'
            for name, value in self._fields.items()
            if isinstance(value, str)
        )
        self.write(f"<action-response{attributes}>")

    def end_response(self) -> None:
        self.write("</action-response>\n")

    def failure(self, code: str, description: str) -> None:
        self.write(
            f'<failure code="{php_escape(code)}">{php_escape(description)}</failure>'
        )

    def take_action(self, body_xml: str = "") -> None:
        """The response of a write that succeeded, with `body_xml` after it."""
        self.start_response()
        self.write("<success/>" + body_xml)
        self.end_response()

    def not_authorized_body(self) -> None:
        self.failure("notauthorized", "Not authorized -- please see race coordinator")

    def not_authorized(self) -> None:
        self.start_response()
        self.not_authorized_body()
        self.end_response()

    def failure_response(self, code: str, description: str) -> None:
        """Replace anything written so far with a whole failure document."""
        self._parts.clear()
        self.start_response()
        self.failure(code, description)
        self.end_response()

    def document(self) -> bytes:
        return (PROLOG + "".join(self._parts)).encode()
