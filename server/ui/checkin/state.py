"""What the check-in page shows (#42): the racer cards, the filter bar's
choices, and the signature of the list.

Reads only, through the roster and racing models: the page and its 10 s poll
write neither the session nor the database.

A card carries every column of PHP's check-in table (`website/js/checkin.js`):
the car number, the partition, the head and car thumbnails, the name, the car
name with its "from" note, the Passed switch with its status text, and the XBS
switch when `use-xbs` is on. It adds what Django knows and PHP does not: the
D36 question for a racer whose group's round is running, and the D32 request
for a place in the final.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field

from django.urls import get_script_prefix

from awards.models import Award
from core.models import RaceSettings
from racing.models import HAS_RESULT, FinalRequest, HeatLane, RosterEntry, Round
from roster.models import Partition, Racer
from roster.services import car_numbers
from roster.services.checkin import round_in_progress_for
from roster.services.racers import CAR_PHOTOS, HEADSHOTS, photo_url
from ui.responses import racer_name

THUMBNAIL = "100x100"
POLL_SECONDS = 10

ORDERS = (
    ("name", "Name"),
    ("car", "Car"),
    ("partition", "Den"),
    ("class", "Class"),
)
STATUSES = (("all", "All"), ("waiting", "Not passed"), ("passed", "Passed"))
# The status text of PHP's table (checkin.js), as a filter.
STATES = (
    ("", "Any status"),
    ("racing", "Racing!"),
    ("late", "Late!"),
    ("unpassed", "Scheduled but not passed"),
    ("unscheduled", "Not scheduled"),
)
_STATE_TEXT = {
    "racing": "Racing!",
    "late": "Late!",
    "unpassed": "Scheduled but not passed",
    "unscheduled": "",
}


def car(number) -> str:
    """A car number padded to three digits (D47)."""
    try:
        return f"{int(number):03d}"
    except TypeError, ValueError:
        return str(number or "")


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _nocase(text: str) -> str:
    return text.translate(_ASCII_LOWER)


@dataclass(frozen=True)
class Filters:
    """The filter bar: the search text, the partition, passed or not, the
    status text, and the order."""

    q: str = ""
    partition: int | None = None
    status: str = "all"
    order: str = "name"
    state: str = ""

    @classmethod
    def from_request(cls, data) -> Filters:
        def text(name):
            value = data.get(name, "")
            return value.strip() if isinstance(value, str) else ""

        try:
            partition = int(text("partition"))
        except ValueError:
            partition = None
        status = text("status")
        order = text("order")
        state = text("state")
        return cls(
            q=text("q")[:100],
            partition=partition,
            status=status if status in dict(STATUSES) else "all",
            order=order if order in dict(ORDERS) else "name",
            state=state if state in _STATE_TEXT else "",
        )

    def shows(self, card: Card) -> bool:
        """The status filter, which needs the card's status."""
        return not self.state or card.status == _STATE_TEXT[self.state]

    def matches(self, racer: Racer) -> bool:
        if self.partition is not None and racer.partition_id != self.partition:
            return False
        if self.status == "passed" and not racer.passed_inspection:
            return False
        if self.status == "waiting" and racer.passed_inspection:
            return False
        if not self.q:
            return True
        needle = self.q.casefold().lstrip("#")
        if needle.isdigit():
            return needle in str(racer.car_number) or needle in car(racer.car_number)
        names = (
            racer_name(racer.first_name, racer.last_name),
            f"{racer.last_name}, {racer.first_name}",
            f"{racer.last_name} {racer.first_name}",
        )
        return any(needle in n.casefold() for n in names)


def _sort_key(order: str):
    def name(r):
        return (_nocase(r.last_name), _nocase(r.first_name), r.pk)

    match order:
        case "car":
            return lambda r: (r.car_number, *name(r))
        case "partition":
            return lambda r: (r.partition.sort_order, *name(r))
        case "class":
            return lambda r: (r.group.sort_order, r.subgroup.sort_order, *name(r))
    return name


@dataclass(frozen=True)
class Card:
    id: int
    car: str
    first_name: str
    last_name: str
    partition: str
    partition_id: int
    group: str
    car_name: str
    note: str
    headshot: str
    car_photo: str
    passed: bool
    # "Racing!", "Scheduled but not passed", "Late!" or "", as PHP shows.
    status: str
    xbs: bool
    exclude: bool
    no_speed_awards: bool
    # The round check-in offers to add the car to (D36), when the racer
    # passed, has no heats there, and nobody answered No.
    round_id: int | None = None
    round_name: str = ""
    # The round a No left the car out of (D36): the dashboard lists it.
    left_out_of: str = ""
    # D32: "can" when the racer may ask for a place in the final, else the
    # request's state, or "".
    final: str = ""
    # The racer has a result, so check-in can no longer change the pass.
    locked: bool = False

    @property
    def name(self) -> str:
        return racer_name(self.first_name, self.last_name)

    @property
    def initials(self) -> str:
        return (self.first_name[:1] + self.last_name[:1]).upper()


@dataclass
class Listing:
    cards: list[Card]
    total: int
    passed: int
    shown: int
    partitions: list[tuple[int, str]]
    use_xbs: bool
    next_numbers: dict[int, int | None] = field(default_factory=dict)
    signature: str = ""


def _round_name(round: Round) -> str:
    return f"{round.group.name}, Round {round.number}"


def _photo(repository: str, folder: str, path: str) -> str:
    url = photo_url(repository, folder, path, THUMBNAIL)
    return get_script_prefix() + url if url else ""


def _round_1_over(event) -> set[int]:
    """The groups whose round 1 has a chart with no unraced heat left."""
    lanes = HeatLane.objects.filter(event=event, round__number=1)
    charted = dict(lanes.values_list("round_id", "round__group_id"))
    unraced = set(lanes.exclude(HAS_RESULT).values_list("round_id", flat=True))
    return {group for round, group in charted.items() if round not in unraced}


def cards_for(event, racers: list[Racer]) -> list[Card]:
    """The cards of `racers`, which carry `group`, `subgroup` and
    `partition`."""
    settings = RaceSettings.objects.get(event=event)
    ids = [r.pk for r in racers]
    scheduled = set(
        HeatLane.objects.filter(event=event, racer_id__in=ids).values_list(
            "racer_id", flat=True
        )
    )
    raced = set(
        HeatLane.objects.filter(event=event, racer_id__in=ids)
        .filter(HAS_RESULT)
        .values_list("racer_id", flat=True)
    )
    raced_round_1 = set(
        HeatLane.objects.filter(event=event, racer_id__in=ids, round__number=1)
        .filter(HAS_RESULT)
        .values_list("racer_id", flat=True)
    )
    group_scheduled = set(
        HeatLane.objects.filter(event=event).values_list("round__group_id", flat=True)
    )
    xbs = set()
    if settings.use_xbs:
        xbs = set(
            Award.objects.filter(
                event=event, name=settings.xbs_award, racer_id__in=ids
            ).values_list("racer_id", flat=True)
        )
    requests = {
        (r.racer_id, r.group_id): r.state
        for r in FinalRequest.objects.filter(event=event, racer_id__in=ids)
    }
    left_out = {
        e.racer_id: e.round
        for e in RosterEntry.objects.filter(
            event=event, racer_id__in=ids, left_out_at__isnull=False
        ).select_related("round__group")
    }
    over = _round_1_over(event)
    cards = []
    for r in racers:
        status = ""
        if r.pk in scheduled:
            status = "Racing!" if r.passed_inspection else "Scheduled but not passed"
        elif r.group_id in group_scheduled:
            status = "Late!"
        final = requests.get((r.pk, r.group_id), "")
        if (
            not final
            and r.passed_inspection
            and r.group_id in over
            and r.pk not in raced_round_1
        ):
            final = "can"
        round = None
        if status == "Late!" and r.passed_inspection and not final:
            round = round_in_progress_for(r)
        left = left_out.get(r.pk)
        if round is not None and left is not None and left.pk == round.pk:
            round = None
        cards.append(
            Card(
                id=r.pk,
                car=car(r.car_number),
                first_name=r.first_name,
                last_name=r.last_name,
                partition=r.partition.name,
                partition_id=r.partition_id,
                group=r.group.name,
                car_name=r.car_name,
                note=r.note,
                headshot=_photo(HEADSHOTS, settings.photo_directory, r.headshot),
                car_photo=_photo(
                    CAR_PHOTOS,
                    settings.car_photo_directory or settings.photo_directory,
                    r.car_photo,
                ),
                passed=r.passed_inspection,
                status=status,
                xbs=r.pk in xbs,
                exclude=r.exclude,
                no_speed_awards=r.not_eligible_for_speed_awards,
                round_id=round.pk if round else None,
                round_name=_round_name(round) if round else "",
                left_out_of=_round_name(left)
                if left is not None and r.pk not in scheduled
                else "",
                final=final,
                locked=r.pk in raced,
            )
        )
    return cards


def racers_of(event):
    return Racer.objects.filter(event=event).select_related(
        "group", "subgroup", "partition"
    )


def card(event, racer_id: int) -> Card | None:
    found = list(racers_of(event).filter(pk=racer_id))
    return cards_for(event, found)[0] if found else None


def partitions(event) -> list[tuple[int, str]]:
    return list(
        Partition.objects.filter(event=event)
        .order_by("sort_order", "id")
        .values_list("pk", "name")
    )


def next_numbers(event) -> dict[int, int | None]:
    """Each partition's next car number (D48: None when its ranges are
    full)."""
    return {
        row["partitionid"]: row["next_carnumber"]
        for row in car_numbers.next_car_numbers(event.id)
    }


def build(event, filters: Filters) -> Listing:
    everyone = list(racers_of(event))
    chosen = sorted(
        (r for r in everyone if filters.matches(r)), key=_sort_key(filters.order)
    )
    in_partition = [
        r
        for r in everyone
        if filters.partition is None or r.partition_id == filters.partition
    ]
    cards = [c for c in cards_for(event, chosen) if filters.shows(c)]
    listing = Listing(
        cards=cards,
        total=len(in_partition),
        passed=sum(r.passed_inspection for r in in_partition),
        shown=len(cards),
        partitions=partitions(event),
        use_xbs=RaceSettings.objects.get(event=event).use_xbs,
        next_numbers=next_numbers(event),
    )
    material = json.dumps(
        {
            "cards": [asdict(c) for c in listing.cards],
            "counts": [listing.total, listing.passed, listing.shown],
            "partitions": listing.partitions,
            "xbs": listing.use_xbs,
            "next": sorted(listing.next_numbers.items()),
            "filters": asdict(filters),
        },
        sort_keys=True,
        default=str,
    )
    listing.signature = hashlib.sha256(material.encode()).hexdigest()[:20]
    return listing
