"""What the judging page shows (#45): the judged awards with their winners,
the racers who can be judged, and the two sheets of tap to assign.

Reads only: the page, its 30 s poll and its sheets write neither the
session nor the database. The racers are the passed ones, as
`judging.php` lists them, less those not eligible for any award
(`exclude`). A racer not eligible for speed awards stays, marked, because
judged awards stay open to them (D33). Whether a racer can win an award is
`awards.eligibility.Eligibility`'s answer, the one `award.winner` and the
assign service give, so the sheet shows the reason before the judge taps
(C31: `judging.js` never applied the subgroup check).
"""

import hashlib
from dataclasses import dataclass, field

from django.urls import get_script_prefix

from awards import balloting
from awards.balloting import Candidate
from awards.editing import refusal_text
from awards.eligibility import Eligibility
from awards.listing import AD_HOC, sort_key
from awards.models import Award, BallotAward
from core.models import RaceSettings
from roster.models import Group, Racer
from roster.services.groups import Labels, labels
from roster.services.racers import CAR_PHOTOS, photo_url
from ui.responses import car, nocase, racer_name

POLL_SECONDS = 30
# `RENDER_JUDGING` in `website/inc/photo-config.inc`.
RENDER_JUDGING = "200x200"
ORDERS = (("car", "Car"), ("name", "Name"), ("class", "Group"))


@dataclass(frozen=True)
class Filters:
    q: str = ""
    order: str = "car"

    @classmethod
    def from_request(cls, data) -> Filters:
        def text(name):
            value = data.get(name, "")
            return value.strip() if isinstance(value, str) else ""

        order = text("order")
        return cls(q=text("q")[:100], order=order if order in dict(ORDERS) else "car")

    def matches(self, card: RacerCard) -> bool:
        if not self.q:
            return True
        needle = self.q.casefold().lstrip("#")
        if needle.isdigit():
            return needle in str(card.car_number) or needle in card.car
        return needle in card.name.casefold()


@dataclass(frozen=True)
class RacerCard:
    id: int
    car: str
    car_number: int
    name: str
    group: str
    subgroup: str
    photo: str
    awards: tuple[str, ...]
    ad_hoc: str
    speed_ineligible: bool


@dataclass(frozen=True)
class AwardCard:
    id: int
    name: str
    award_type: str
    scope: str
    winner: RacerCard | None
    winner_name: str = ""


@dataclass
class Board:
    awards: list[AwardCard]
    racers: list[RacerCard]
    shown: list[RacerCard]
    labels: Labels
    use_groups: bool
    use_subgroups: bool
    filters: Filters
    signature: str = ""


@dataclass(frozen=True)
class Choice:
    """An award on a racer's sheet: given to this racer, open to them, or
    refused with the reason."""

    award: AwardCard
    mine: bool
    reason: str = ""


@dataclass
class RacerSheet:
    racer: RacerCard
    choices: list[Choice] = field(default_factory=list)


@dataclass
class AwardSheet:
    award: AwardCard
    racers: list[RacerCard] = field(default_factory=list)


class _Event:
    """The event's settings, racers and awards, read once."""

    def __init__(self, event):
        self.event = event
        self.settings = RaceSettings.objects.get(event=event)
        self.labels = labels(self.settings)
        self.prefix = get_script_prefix()
        self.racer_rows = list(
            Racer.objects.filter(event=event, passed_inspection=True, exclude=False)
            .select_related("group", "subgroup", "partition")
            .order_by("pk")
        )
        self.award_rows = sorted(
            Award.objects.filter(event=event).select_related(
                "award_type", "racer", "group", "subgroup"
            ),
            key=sort_key,
        )
        self.held: dict[int, list[str]] = {}
        self.ad_hoc: dict[int, str] = {}
        for a in self.award_rows:
            if a.racer_id is None:
                continue
            if a.award_type_id == AD_HOC:
                self.ad_hoc[a.racer_id] = a.name
            else:
                self.held.setdefault(a.racer_id, []).append(a.name)
        self.cards = {r.pk: self.card(r) for r in self.racer_rows}

    def card(self, r: Racer) -> RacerCard:
        photo = photo_url(
            CAR_PHOTOS, self.settings.car_photo_directory, r.car_photo, RENDER_JUDGING
        )
        return RacerCard(
            id=r.pk,
            car=car(r.car_number),
            car_number=r.car_number,
            name=racer_name(r.first_name, r.last_name),
            group=r.group.name,
            subgroup=r.subgroup.name,
            photo=self.prefix + photo if photo else "",
            awards=tuple(self.held.get(r.pk, ())),
            ad_hoc=self.ad_hoc.get(r.pk, ""),
            speed_ineligible=r.not_eligible_for_speed_awards,
        )

    def award_card(self, a: Award) -> AwardCard:
        if a.subgroup_id:
            scope = (
                f"{a.group.name} · {a.subgroup.name}" if a.group else a.subgroup.name
            )
        elif a.group_id:
            scope = a.group.name
        else:
            scope = self.labels.supergroup
        winner = self.cards.get(a.racer_id) if a.racer_id else None
        name = ""
        if a.racer is not None:
            who = racer_name(a.racer.first_name, a.racer.last_name)
            name = f"{car(a.racer.car_number)} {who}"
        return AwardCard(a.pk, a.name, a.award_type.name, scope, winner, name)

    def judged(self) -> list[Award]:
        return [a for a in self.award_rows if a.award_type_id != AD_HOC]


def _sorted(racers: list[Racer], order: str) -> list[Racer]:
    def name(r):
        return (nocase(r.last_name), nocase(r.first_name), r.pk)

    if order == "name":
        return sorted(racers, key=name)
    if order == "class":
        return sorted(racers, key=lambda r: (r.group.sort_order, *name(r)))
    return sorted(racers, key=lambda r: (r.car_number, *name(r)))


def build(event, filters: Filters) -> Board:
    found = _Event(event)
    racers = [found.cards[r.pk] for r in _sorted(found.racer_rows, filters.order)]
    board = Board(
        awards=[found.award_card(a) for a in found.judged()],
        racers=racers,
        shown=[c for c in racers if filters.matches(c)],
        labels=found.labels,
        use_groups=Group.objects.filter(event=event).count() > 1,
        use_subgroups=found.settings.use_subgroups,
        filters=filters,
    )
    material = repr((board.awards, board.shown, board.use_groups, board.use_subgroups))
    board.signature = hashlib.sha256(material.encode()).hexdigest()[:20]
    return board


def racer_sheet(event, racer_id: int) -> RacerSheet | None:
    """A racer's sheet: every judged award, given to them, open to them, or
    refused with the reason. None for a racer the page does not list."""
    found = _Event(event)
    racer = next((r for r in found.racer_rows if r.pk == racer_id), None)
    if racer is None:
        return None
    eligibility = Eligibility(event.id)
    sheet = RacerSheet(found.cards[racer.pk])
    for a in found.judged():
        card = found.award_card(a)
        if a.racer_id == racer.pk:
            sheet.choices.append(Choice(card, mine=True))
            continue
        refusal = eligibility.refusal(a, racer)
        if refusal is not None:
            reason = refusal_text(refusal, a, racer)
        elif a.racer_id is not None:
            reason = f"Given to {card.winner_name}"
        else:
            reason = ""
        sheet.choices.append(Choice(card, mine=False, reason=reason))
    return sheet


def award_sheet(event, award_id: int, order: str = "car") -> AwardSheet | None:
    """An award's sheet: its winner, and the listed racers who can win it,
    those whose group, subgroup and partition its lists hold (D35)."""
    found = _Event(event)
    award = next((a for a in found.judged() if a.pk == award_id), None)
    if award is None:
        return None
    eligibility = Eligibility(event.id)
    return AwardSheet(
        found.award_card(award),
        [
            found.cards[r.pk]
            for r in _sorted(eligibility.winners(award, found.racer_rows), order)
        ],
    )


# ----- the ballot sheet (#54) -----

DEPTHS = balloting.DEPTH_CHOICES


@dataclass(frozen=True)
class BallotRow:
    """A judged award on the ballot sheet: its votes per ballot, 0 for off
    the ballot, and its tally, most votes first."""

    award: AwardCard
    depth: int
    candidates: list[Candidate]

    @property
    def depths(self) -> tuple[int, ...]:
        return DEPTHS if self.depth in DEPTHS else (*DEPTHS, self.depth)


@dataclass
class BallotSheet:
    is_open: bool
    rows: list[BallotRow]


def ballot_sheet(event) -> BallotSheet:
    """`judging.php`'s Manage Ballot dialog (`website/judging.php:160` to
    `:177`) and its tallies (`on_votes_click()`, `website/js/judging.js:93`):
    the open switch, each judged award's votes per ballot and its votes.
    The dialog's ballot password field is gone: a voting token, not the
    password, lets a voter in (#206, D86)."""
    found = _Event(event)
    depths = dict(
        BallotAward.objects.filter(event=event).values_list("award_id", "depth")
    )
    tallied = {t.award_id: t.candidates for t in balloting.tallies(event)}
    return BallotSheet(
        is_open=balloting.is_open(event),
        rows=[
            BallotRow(found.award_card(a), depths.get(a.pk, 0), tallied.get(a.pk, []))
            for a in found.judged()
        ],
    )
