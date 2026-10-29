"""The award lists the legacy API answers with (#33): `json_out_awards()` in
`website/inc/json-awards.inc` and `query.award.current.inc`.

A pack-wide award lists `classid` 0 and an award with no subgroup `rankid`
0, and an award with no winner `racerid` 0. PHP lists the null it stored
for an award made with no `class_and_rank`, which the awards editor reads
as an unknown class; Django stores no such difference (deviations.json).
"""

import time
from collections import Counter

from awards.models import Award, AwardType, BallotAward, Vote
from core.models import RaceSettings, RaceState
from racing.engine.phpcompat import php_numeric
from racing.services.polls import mangled_name
from racing.services.standings import current_award as current_speed_award
from racing.services.standings import standings_snapshot
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Racer,
    Subgroup,
)
from roster.services import structure
from roster.services.racers import CAR_PHOTOS, HEADSHOTS, photo_url

AD_HOC = 6  # AD_HOC_AWARDTYPEID in website/inc/awards.inc
RENDER_CROPPED = "cropped"


def award_types(include_ad_hoc: bool) -> list[dict]:
    types = AwardType.objects.order_by("id")
    if not include_ad_hoc:
        types = types.exclude(id=AD_HOC)
    return [{"awardtypeid": t.id, "awardtype": t.name} for t in types]


def _eligibility(event):
    """`mark_award_eligibility()`: a function from an award to the group and
    subgroup ids its winner may come from, in class and rank order."""
    groups = sorted(
        Group.objects.filter(event=event),
        key=lambda g: (g.sort_order, g.name, g.pk),
    )
    group_order = {g.pk: n for n, g in enumerate(groups)}
    subgroups = sorted(
        Subgroup.objects.filter(event=event),
        key=lambda s: (group_order[s.group_id], s.sort_order, s.name, s.pk),
    )
    every_group = [g.pk for g in groups]
    every_subgroup = [s.pk for s in subgroups]
    constituents: dict[int, list[int]] = {}
    for aggregate, constituent in (
        GroupConstituent.objects.filter(event=event)
        .order_by("id")
        .values_list("aggregate_id", "constituent_id")
    ):
        constituents.setdefault(aggregate, []).append(constituent)
    filters: dict[int, list[int]] = {}
    for aggregate, subgroup in (
        GroupConstituentSubgroup.objects.filter(event=event)
        .order_by("id")
        .values_list("aggregate_id", "subgroup_id")
    ):
        filters.setdefault(aggregate, []).append(subgroup)

    def closure(aggregate: int) -> list[int]:
        """`compute_aggregate_constituents_tc()`, in PHP's order."""
        found = list(constituents[aggregate])
        for member in found:
            found.extend(c for c in constituents.get(member, ()) if c not in found)
        return found

    def eligible(award: Award) -> tuple[list[int], list[int]]:
        group_id = award.group_id
        aggregate = group_id in constituents or group_id in filters
        if group_id is None:
            groups_ok = every_group
        elif aggregate:
            groups_ok = closure(group_id) if group_id in constituents else []
        else:
            groups_ok = [group_id]
        if award.subgroup_id is not None:
            subgroups_ok = [award.subgroup_id]
        elif aggregate and filters.get(group_id):
            subgroups_ok = filters[group_id]
        else:
            subgroups_ok = every_subgroup
        return groups_ok, subgroups_ok

    return eligible


def _votes(event, award_ids) -> dict[int, list[dict]]:
    """Each award's votes by racer, most first."""
    counts = Counter(
        Vote.objects.filter(event=event, award_id__in=award_ids).values_list(
            "award_id", "racer_id"
        )
    )
    racers = Racer.objects.in_bulk({racer for _, racer in counts})
    tallies: dict[int, list[dict]] = {}
    for (award_id, racer_id), score in counts.most_common():
        racer = racers[racer_id]
        tallies.setdefault(award_id, []).append(
            {
                "racerid": racer_id,
                "score": score,
                "carnumber": racer.car_number,
                "firstname": racer.first_name,
                "lastname": racer.last_name,
            }
        )
    return tallies


def awards(event, include_ad_hoc: bool) -> list[dict]:
    """`json_all_awards()`: every award by sort, type name, then name."""
    rows = Award.objects.filter(event=event).select_related("award_type", "racer")
    if not include_ad_hoc:
        rows = rows.exclude(award_type_id=AD_HOC)
    rows = sorted(rows, key=lambda a: (a.sort, a.award_type.name, a.name, a.pk))
    eligible = _eligibility(event)
    depths = dict(
        BallotAward.objects.filter(event=event).values_list("award_id", "depth")
    )
    votes = _votes(event, [a.pk for a in rows])
    listed = []
    for award in rows:
        groups_ok, subgroups_ok = eligible(award)
        racer = award.racer
        listed.append(
            {
                "awardid": award.pk,
                "awardname": award.name,
                "awardtype": award.award_type.name,
                "awardtypeid": award.award_type_id,
                "adhoc": award.award_type_id == AD_HOC,
                "classid": award.group_id or 0,
                "eligible-classids": groups_ok,
                "eligible-rankids": subgroups_ok,
                "rankid": award.subgroup_id or 0,
                "racerid": award.racer_id or 0,
                "firstname": racer.first_name if racer else "",
                "lastname": racer.last_name if racer else "",
                "carnumber": racer.car_number if racer else None,
                "sort": award.sort,
                "ballot_depth": depths.get(award.pk, 0),
                "votes": votes.get(award.pk, []),
            }
        )
    return listed


def speed_awards(event) -> list[dict]:
    """`json_speed_awards()`."""
    listed = []
    for award in standings_snapshot(event).speed_awards.awards:
        entry = {
            "type": "speed-award",
            "racerid": award.racer_id,
            "key": award.key,
            "place": award.place,
            "category": str(award.category),
        }
        if award.group_id is not None:
            entry["classid"] = award.group_id
        if award.subgroup_id is not None:
            entry["rankid"] = award.subgroup_id
        listed.append(entry)
    return listed


def award_lists(event, include_ad_hoc: bool) -> dict:
    """`json_out_awards()`: the four keys, in PHP's order."""
    return {
        "award-types": award_types(include_ad_hoc),
        "awards": awards(event, include_ad_hoc),
        "speed-awards": speed_awards(event),
        "classes": structure.classes(event),
    }


def _row_id(text: str) -> int:
    """The id `awardid = :awardid` finds for text: SQLite compares numeric
    text with an integer column by its number. -1 for no row."""
    number = php_numeric(text)
    if number is None or not 0 < number < 2**63 or number != int(number):
        return -1
    return int(number)


class UnrecognizedKey(Exception):
    """A `current_award_key` that names neither an award nor a speed award."""


def current(event, started: float) -> dict | None:
    """`query=award.current`: the award being presented, or None when no
    award, or no winner, goes with the key. `started` is the request's
    start, for PHP's `duration`. Raises `UnrecognizedKey`."""
    state = RaceState.objects.get(event=event)
    key = state.current_award_key
    parts = key.split("-")
    if len(parts) < 2:
        return None
    if parts[0] == "award":
        award = (
            Award.objects.filter(event=event, pk=_row_id(parts[1]))
            .select_related("racer__subgroup")
            .first()
        )
        if award is None or award.racer is None:
            return None
        name, racer = award.name, award.racer
    elif parts[0] == "speed":
        found = current_speed_award(event)
        if found is None:
            return None
        key, name = found.key, found.name
        racer = Racer.objects.select_related("subgroup").get(pk=found.racer_id)
    else:
        raise UnrecognizedKey(key)

    settings = RaceSettings.objects.get(event=event)
    shown = {
        "key": key,
        "awardname": name,
        "reveal": state.reveal_current_award,
        "carnumber": racer.car_number,
        "carname": racer.car_name,
        "recipient": mangled_name(
            racer.first_name, racer.last_name, settings.name_style
        ),
    }
    if settings.use_subgroups:
        shown["subgroup"] = racer.subgroup.name
    if racer.headshot:
        shown["headphoto"] = photo_url(
            HEADSHOTS, settings.photo_directory, racer.headshot, RENDER_CROPPED
        )
    if racer.car_photo:
        shown["carphoto"] = photo_url(
            CAR_PHOTOS, settings.car_photo_directory, racer.car_photo, RENDER_CROPPED
        )
    shown["duration"] = time.monotonic() - started
    return shown
