"""What the vote page shows (#54): the awards a voter may vote on, the
racers who can win each, and the three orders of the racer grid.

`vote.php` lists the racers who passed check-in and are eligible for awards
(`passedinspection = 1 AND exclude = 0`, `website/vote.php:67`), with no
names (`website/vote.php:152`), and every judged award
(`all_awards(false)`, `website/vote.php:101`). Which of those awards are on
the ballot, and their depth, come from `ballot.get` in the browser, as in
`vote.js` (`get_ballot()`, `website/js/vote.js:172`). Each award's racers
are `Eligibility`'s answer, the rule `vote.cast` checks (S14, D35), where
`click_one_award()` filters by the page's `data-eligible-classids` and
`data-eligible-rankids` (`website/js/vote.js:65` to `:71`) and ignores D35's
partition lists.

Reads only: the page writes neither the session nor the database. The
voter's ballot is `ballot.get`'s, which the page asks for once it shows.
"""

from dataclasses import dataclass

from django.urls import get_script_prefix

from awards import balloting
from awards.eligibility import Eligibility
from awards.listing import AD_HOC, sort_key
from awards.models import Award
from core.models import RaceSettings
from media import photos
from roster.models import Racer
from roster.services.groups import labels
from roster.services.racers import CAR_PHOTOS, photo_url
from ui.responses import car, nocase

POLL_SECONDS = 5
# `RENDER_JUDGING` and `RENDER_WORKING` in `website/inc/photo-config.inc:12`,
# `:24`: the grid's thumbnails and the racer sheet's photo
# (`website/vote.php:145`, `:142`).
RENDER_THUMB = "200x200"
# `?order=` as `vote.php` reads it (`website/vote.php:42` to `:45`).
ORDERS = ("car", "class", "name")


def order_of(raw) -> str:
    return raw if raw in ORDERS else "car"


@dataclass(frozen=True)
class VoteRacer:
    id: int
    car: str
    group: str
    subgroup: str
    thumb: str
    photo: str


@dataclass(frozen=True)
class VoteAward:
    id: int
    name: str
    racers: list[int]


@dataclass
class Page:
    is_open: bool
    awards: list[VoteAward]
    racers: list[VoteRacer]
    orders: dict[str, list[int]]
    use_subgroups: bool
    group_label: str
    order: str

    def config(self) -> dict:
        """What the page's script reads."""
        return {
            "open": self.is_open,
            "order": self.order,
            "orders": self.orders,
            "awards": {a.id: {"name": a.name, "racers": a.racers} for a in self.awards},
            "racers": {
                r.id: {
                    "car": r.car,
                    "group": r.group,
                    "subgroup": r.subgroup if self.use_subgroups else "",
                    "thumb": r.thumb,
                    "photo": r.photo,
                }
                for r in self.racers
            },
        }


def _orders(racers: list[Racer]) -> dict[str, list[int]]:
    """`vote.php`'s three ORDER BYs (`website/vote.php:68` to `:71`), with
    the racer id last so the order is total."""

    def name(r):
        return (nocase(r.last_name), nocase(r.first_name), r.pk)

    return {
        "car": [r.pk for r in sorted(racers, key=lambda r: (r.car_number, *name(r)))],
        "class": [
            r.pk for r in sorted(racers, key=lambda r: (r.group.sort_order, *name(r)))
        ],
        "name": [r.pk for r in sorted(racers, key=name)],
    }


def build(event, order: str = "car") -> Page:
    settings = RaceSettings.objects.get(event=event)
    prefix = get_script_prefix()
    racers = list(
        Racer.objects.filter(event=event, passed_inspection=True, exclude=False)
        .select_related("group", "subgroup")
        .order_by("pk")
    )

    repo = photos.repository(event, "car")

    def thumb(r):
        found = photo_url(
            CAR_PHOTOS, settings.car_photo_directory, r.car_photo, RENDER_THUMB
        )
        return prefix + found if found else ""

    def large(r):
        found = photos.page_url(repo, r.car_photo, photos.WORK) if r.car_photo else ""
        return prefix + found if found else ""

    eligibility = Eligibility(event.id)
    awards = sorted(
        Award.objects.filter(event=event)
        .exclude(award_type_id=AD_HOC)
        .select_related("award_type"),
        key=sort_key,
    )
    return Page(
        is_open=balloting.is_open(event),
        awards=[
            VoteAward(a.pk, a.name, [r.pk for r in eligibility.winners(a, racers)])
            for a in awards
        ],
        racers=[
            VoteRacer(
                r.pk,
                car(r.car_number),
                r.group.name,
                r.subgroup.name,
                thumb(r),
                large(r),
            )
            for r in racers
        ],
        orders=_orders(racers),
        use_subgroups=settings.use_subgroups,
        group_label=labels(settings).group,
        order=order,
    )
