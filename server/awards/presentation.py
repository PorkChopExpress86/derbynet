"""The awards presentation console's list and its writes (#45):
`compute_awards_for_presentation()` in `website/inc/awards.inc`, and the
`award.present` posts of `website/js/awards-presentation.js`.

The list is every speed award, then every judged and ad hoc award, with or
without a winner, by sort and then the winner's name. A judged award sorts
after every speed award (`reserved_overall`). The console filters it by
award type and by the pack, a group or a subgroup, as the two selects of
`awards-presentation.php` do.

Every write takes the `RaceState` lock and reads the list again under it,
so a key the page sends back is checked as a new request: a key that is no
longer listed is refused, and the same post twice changes nothing more.
"""

from dataclasses import dataclass, field

from django.db import transaction

from awards.editing import AwardFailure, present
from awards.listing import AD_HOC, award_key, sort_key
from awards.models import Award, AwardType
from core import race_settings
from core.models import RaceSettings, RaceState
from core.services import lock_race_state
from kiosks.models import Kiosk
from racing.services.polls import mangled_name
from racing.services.standings import standings_snapshot
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Racer,
    Subgroup,
)

ALL = "all"
PACK = "pack"
# The type `add_speed_group()` gives a speed award, "Speed Trophy".
SPEED_TROPHY = 5
# The kiosk page that shows the award being presented, as PHP's
# `page LIKE '%award%present%'` finds it.
AWARD_KIOSK_PAGE = r"award.*present"


@dataclass(frozen=True)
class Listed:
    """One award on the console. `group_id` and `subgroup_id` are 0 for
    none, and `subgroup_id` is 0 too while subgroups are off, as PHP's
    `data-classid` and `data-rankid` are."""

    key: str
    name: str
    award_type_id: int
    group_id: int
    subgroup_id: int
    group: str
    subgroup: str
    racer_id: int | None
    car_number: int | None
    recipient: str
    car_name: str
    sort: int
    last_name: str = ""
    first_name: str = ""

    @property
    def ad_hoc(self) -> bool:
        return self.award_type_id == AD_HOC

    def shown_by(self, award_type: int | None, scope: str) -> bool:
        """The two selects: a type, or None for all; and `all`, `pack`,
        `g<id>` for a group's awards, its subgroups' included, or `s<id>`
        for a subgroup's."""
        if award_type is not None and self.award_type_id != award_type:
            return False
        if scope == PACK:
            return self.group_id == 0 and self.subgroup_id == 0
        if scope.startswith("g"):
            return str(self.group_id) == scope[1:]
        if scope.startswith("s"):
            return str(self.subgroup_id) == scope[1:]
        return scope == ALL


@dataclass
class Presentation:
    awards: list[Listed]
    types: list[tuple[int, str]]
    # (value, label, indented) for the group select.
    scopes: list[tuple[str, str, bool]]
    # The pack standings choice, or empty when PHP shows none.
    calc_choices: list[tuple[str, str]] = field(default_factory=list)
    calc: str = "0"
    supergroup_label: str = "Pack"

    def filtered(self, award_type: int | None, scope: str) -> list[Listed]:
        return [a for a in self.awards if a.shown_by(award_type, scope)]

    def keys(self) -> set[str]:
        return {a.key for a in self.awards}

    @property
    def rows(self) -> list[tuple[int, int, int]]:
        """What the page's filter chips read of each award."""
        return [(a.award_type_id, a.group_id, a.subgroup_id) for a in self.awards]


def _aggregates(event) -> list[Group]:
    ids = set(
        GroupConstituent.objects.filter(event=event).values_list(
            "aggregate_id", flat=True
        )
    ) | set(
        GroupConstituentSubgroup.objects.filter(event=event).values_list(
            "aggregate_id", flat=True
        )
    )
    return sorted(
        Group.objects.filter(event=event, pk__in=ids),
        key=lambda g: (g.sort_order, g.name, g.pk),
    )


def presentation(event) -> Presentation:
    """The console's list and its filter choices, read once."""
    settings = RaceSettings.objects.get(event=event)
    groups = sorted(
        Group.objects.filter(event=event), key=lambda g: (g.sort_order, g.name, g.pk)
    )
    group_names = {g.pk: g.name for g in groups}
    subgroups = sorted(
        Subgroup.objects.filter(event=event),
        key=lambda s: (s.sort_order, s.name, s.pk),
    )
    subgroup_names = {s.pk: s.name for s in subgroups}
    use_subgroups = settings.use_subgroups
    style = settings.name_style

    def listed(key, name, type_id, group_id, subgroup_id, racer, sort) -> Listed:
        group_id = group_id or 0
        subgroup_id = (subgroup_id or 0) if use_subgroups else 0
        return Listed(
            key=key,
            name=name,
            award_type_id=type_id,
            group_id=group_id,
            subgroup_id=subgroup_id,
            group=group_names.get(group_id, ""),
            subgroup=subgroup_names.get(subgroup_id, ""),
            racer_id=racer.pk if racer else None,
            car_number=racer.car_number if racer else None,
            recipient=mangled_name(racer.first_name, racer.last_name, style)
            if racer
            else "",
            car_name=racer.car_name if racer else "",
            sort=sort,
            last_name=racer.last_name if racer else "",
            first_name=racer.first_name if racer else "",
        )

    speed = standings_snapshot(event).speed_awards
    racers = Racer.objects.in_bulk({a.racer_id for a in speed.awards})
    rows = [
        listed(
            a.key,
            a.name,
            SPEED_TROPHY,
            a.group_id,
            a.subgroup_id,
            racers.get(a.racer_id),
            a.sort,
        )
        for a in speed.awards
    ]
    judged = sorted(
        Award.objects.filter(event=event).select_related("award_type", "racer"),
        key=sort_key,
    )
    rows += [
        listed(
            award_key(a.pk),
            a.name,
            a.award_type_id,
            a.group_id,
            a.subgroup_id,
            a.racer,
            speed.reserved_overall + a.sort,
        )
        for a in judged
    ]
    rows.sort(key=lambda r: (r.sort, r.last_name, r.first_name))

    per_group = {r.group_id for r in rows if r.group_id}
    per_subgroup = {r.subgroup_id for r in rows if r.subgroup_id}
    scopes = [(ALL, "All", False)]
    if any(r.group_id == 0 for r in rows):
        scopes.append((PACK, settings.supergroup_label, False))
    for g in groups:
        if g.pk in per_group:
            scopes.append((f"g{g.pk}", g.name, False))
        if use_subgroups:
            scopes += [
                (f"s{s.pk}", s.name, True)
                for s in subgroups
                if s.group_id == g.pk and s.pk in per_subgroup
            ]

    found = Presentation(
        awards=rows,
        types=list(AwardType.objects.order_by("name").values_list("id", "name")),
        scopes=scopes,
        supergroup_label=settings.supergroup_label,
    )
    aggregates = _aggregates(event)
    if (settings.n_pack_trophies or 0) > 0 and aggregates:
        found.calc_choices = [("0", "Calculate normally")] + [
            (str(g.pk), f"Use {g.name} group") for g in aggregates
        ]
        found.calc = race_settings.read(event, "full-field-calc") or "0"
    return found


def award_kiosks(event) -> int:
    """How many kiosks show the award being presented."""
    return Kiosk.objects.filter(event=event, page__iregex=AWARD_KIOSK_PAGE).count()


def present_award(event, key: str) -> Listed:
    """Tap an award: the kiosks show it, hidden until Reveal, as
    `on_choose_award()` posts `key` with `reveal=0`. A key that is no
    longer listed is refused."""
    with transaction.atomic():
        lock_race_state(event.id)
        found = next((a for a in presentation(event).awards if a.key == key), None)
        if found is None:
            raise AwardFailure("gone", "That award is no longer on the list.")
        present(event, key=key, reveal=False)
    return found


def present_next(event, award_type: int | None, scope: str) -> Listed:
    """Next: the award after the one being presented in the filtered list,
    or the first when the one presented is not in it."""
    with transaction.atomic():
        lock_race_state(event.id)
        listed = presentation(event)
        if award_type is not None and award_type not in dict(listed.types):
            raise AwardFailure("filter", "Pick a type from the list.")
        if scope not in {value for value, _, _ in listed.scopes}:
            raise AwardFailure("filter", "Pick who the awards are for from the list.")
        shown = listed.filtered(award_type, scope)
        if not shown:
            raise AwardFailure("none", "No award matches these filters.")
        current = RaceState.objects.get(event=event).current_award_key
        keys = [a.key for a in shown]
        at = keys.index(current) + 1 if current in keys else 0
        if at >= len(shown):
            raise AwardFailure("last", "That was the last award in the list.")
        present(event, key=shown[at].key, reveal=False)
    return shown[at]


def reveal(event, on: bool) -> None:
    """The Reveal switch, as `on_reveal()` posts `reveal` alone."""
    present(event, reveal=on)


def clear(event) -> None:
    """Clear: no award on the kiosks, as `on_clear_awards()` posts an empty
    `key` with `reveal=0`. The award kiosk no longer clears it when it loads
    (#52), so this is the way to blank it."""
    present(event, key="", reveal=False)
