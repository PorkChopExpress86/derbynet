"""Feeds an event to the Django standings engine and compares what it gives
with what `harness.php` recorded from PHP.

Every racer's partition counts for all-partition awards, and the only award
setting is `exclude`, which is "not eligible for any award". Django keeps an
excluded racer's rows with no place; PHP leaves them out, so the comparison
drops them from Django's rows.

Two differences are deliberate (#20). Q7: with `one-trophy-per`, PHP awards
nobody and skips a racer when the first racer of a tie already holds an
award; Django awards the remaining racers in rung order. Q8: PHP orders the
aggregate groups with a comparator that is not transitive; Django uses a
real topological sort. An order difference counts as Q8 only when PHP's
order is not topological and Django's is. An award difference counts as
Q8 when Django, given PHP's order, gives exactly PHP's awards, and as Q7
when a model of the award loop gives Django's awards with Django's tie
indexing and exactly PHP's with PHP's (`_ModelAwarding`), and PHP warned
that it read a missing racer.
"""

import json
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
from unittest import mock

from racing.engine.phpcompat import ascii_fold
from racing.engine.standings import finalists, oracle, speed_awards
from racing.engine.standings.eligibility import AwardSettings
from racing.engine.standings.ordinals import nth_fastest
from racing.engine.standings.scoring import Scoring

CATEGORIES = (
    "rows",
    "ladders",
    "catalog_counts",
    "catalog",
    "class_keys",
    "rank_keys",
    "topological_order",
    "awards",
    "reserved_overall",
    "finalists",
    "current",
    "current_odd_keys",
)
LISTS = (
    "rows",
    "ladders",
    "catalog_counts",
    "catalog",
    "class_keys",
    "rank_keys",
    "finalists",
)


def build_event(ev: dict) -> tuple[oracle.Event, speed_awards.TrophySettings]:
    s = ev["settings"]
    racers = {r["racerid"]: r for r in ev["RegistrationInfo"]}
    with_result = [
        c
        for c in ev["RaceChart"]
        if c["finishtime"] is not None or c["finishplace"] is not None
    ]
    entered = {(r["roundid"], r["racerid"]) for r in ev["Roster"]}

    def finished(roundid: int) -> bool:
        lanes = [c for c in ev["RaceChart"] if c["roundid"] == roundid]
        return bool(lanes) and all(
            c["finishtime"] is not None or c["finishplace"] is not None
            for c in lanes
            if c["racerid"] is not None
        )

    def own(n: int) -> int | None:
        return n if n >= 0 else None

    event = oracle.Event(
        groups=tuple(
            oracle.Group(
                id=c["classid"],
                name=c["class"],
                sort_order=c["sortorder"],
                n_trophies=own(c["ntrophies"]),
                constituents=tuple(c["constituents"]),
                subgroup_ids=tuple(c["rankids"]),
            )
            for c in sorted(ev["Classes"], key=lambda c: c["classid"])
        ),
        subgroups=tuple(
            oracle.Subgroup(
                r["rankid"],
                r["classid"],
                r["rank"],
                r["sortorder"],
                own(r["ntrophies"]),
            )
            for r in sorted(ev["Ranks"], key=lambda r: r["rankid"])
        ),
        rounds=tuple(
            oracle.Round(r["roundid"], r["classid"], r["round"], finished(r["roundid"]))
            for r in sorted(ev["Rounds"], key=lambda r: r["roundid"])
        ),
        racers=tuple(
            oracle.Racer(
                id=r["racerid"],
                first_name=r["firstname"],
                last_name=r["lastname"],
                car_number=r["carnumber"],
                car_name=r["carname"],
                group_id=r["classid"],
                subgroup_id=r["rankid"],
                awards=AwardSettings(not_eligible_for_any_award=bool(r["exclude"])),
            )
            for r in sorted(racers.values(), key=lambda r: r["racerid"])
            if r["passedinspection"]
        ),
        results=tuple(
            oracle.Result(c["roundid"], c["racerid"], c["finishtime"], c["finishplace"])
            for c in with_result
            if (c["roundid"], c["racerid"]) in entered
        ),
        settings=oracle.Settings(
            use_points=s["use-points"] == "1",
            scoring=Scoring(int(s["scoring"])),
            lane_count=int(s["lane_count"]),
            use_subgroups=s["use-subgroups"] == "1",
            full_field_calc=int(s["full-field-calc"]),
            supergroup_label=s["supergroup-label"],
        ),
    )
    trophies = speed_awards.TrophySettings(
        n_pack=int(s.get("n-pack-trophies", 3)),
        n_group=int(s.get("n-den-trophies", 3)),
        n_subgroup=int(s.get("n-rank-trophies", 0)),
        one_trophy_per=s["one-trophy-per"] == "1",
    )
    return event, trophies


class _ModelAwarding(speed_awards._Awarding):
    """A model of `add_speed_group` in which only the tie indexing varies.
    With `php_indexing`, the remaining racers of a rung keep their positions
    in the rung and the loop reads positions 0 to count - 1, so a missing
    position awards nobody (Q7). Without it they get the place in rung
    order, as Django does. An award difference is Q7 only when the model
    without PHP's indexing gives Django's awards and the model with it
    gives PHP's."""

    php_indexing = False

    def award(
        self, n, bias, group_id, subgroup_id, label, ladder, all_partitions=False
    ):
        racers = self.standings.racers
        place = 0
        rung_index = 0
        while place < n:
            if rung_index >= len(ladder):
                place += 1
                rung_index += 1
                continue
            rung = ladder[rung_index]
            rung_index += 1
            remaining = {
                i: r
                for i, r in enumerate(rung)
                if r not in self.holders
                and racers[r].awards.why_not(all_partitions=all_partitions) is None
            }
            if not remaining:
                continue
            if not self.php_indexing:
                remaining = dict(enumerate(remaining.values()))
            name = nth_fastest(1 + place, label)
            if len(remaining) > 1:
                name += " (Tie)"
            for i in range(len(remaining)):
                racer_id = remaining.get(i)
                letter = ""
                if len(rung) > 1:
                    letter = (
                        chr(ord("a") + i)
                        if self.php_indexing
                        else speed_awards.tie_letters(i)
                    )
                key = f"speed-{1 + place}{letter}"
                if group_id is not None:
                    key += f"-{group_id}"
                if subgroup_id is not None:
                    key += f"-{subgroup_id}"
                self.awards.append(
                    speed_awards.SpeedAward(
                        key,
                        name,
                        1 + place,
                        bias + place,
                        racer_id,
                        group_id,
                        subgroup_id,
                    )
                )
                self.holders.append(racer_id)
            place += 1


class _PhpQ7Awarding(_ModelAwarding):
    php_indexing = True


def _awards_out(awards: speed_awards.SpeedAwards) -> list[dict]:
    return [
        {
            "key": a.key,
            "name": a.name,
            "place": a.place,
            "sort": a.sort,
            "racerid": a.racer_id,
            "classid": a.group_id,
            "rankid": a.subgroup_id,
        }
        for a in awards.awards
    ]


def _current_out(standings, awards, keys: list[str]) -> list[dict | None]:
    out = []
    for key in keys:
        current = speed_awards.current_speed_award(key, standings, awards.awards)
        if current is None or current.racer_id is None:
            out.append(None)
        else:
            out.append(
                {
                    "key": current.key,
                    "awardname": current.name,
                    "racerid": current.racer_id,
                }
            )
    return out


def _speed_awards_as(
    standings, trophies, *, awarding=None, order: list[int] | None = None
):
    with ExitStack() as stack:
        if awarding is not None:
            stack.enter_context(mock.patch.object(speed_awards, "_Awarding", awarding))
        if order is not None:
            stack.enter_context(
                mock.patch.object(
                    speed_awards,
                    "_aggregate_order",
                    lambda s: [s.groups[i] for i in order],
                )
            )
        return speed_awards.speed_awards(standings, trophies)


def django_side(ev: dict, current_keys: list[str]) -> dict:
    event, trophies = build_event(ev)
    standings = oracle.Standings(event)
    awards = speed_awards.speed_awards(standings, trophies)
    rows = [
        {
            "roundid": r.round.id,
            "racerid": r.racer.id,
            "base": r.summary.base,
            "score": r.summary.score,
            "avg": r.summary.avg,
            "best": r.summary.best,
            "worst": r.summary.worst,
            "nonfinal": r.nonfinal,
            "outcome": [[k, p] for k, p in r.outcome.items()],
        }
        for r in standings.rows
        if not r.racer.awards.not_eligible_for_any_award
    ]
    cuts = []
    for req in ev["requests"]:
        if req["kind"] == "follow_on":
            cut = finalists.follow_on(
                standings, req["classid"], bucketed=req["bucketed"], limit=req["limit"]
            )
        elif req["kind"] == "classes":
            cut = finalists.from_groups(
                standings,
                req["classids"],
                bucketed=req["bucketed"],
                limit=req["limit"],
                for_group=0,
            )
        else:
            cut = finalists.from_subgroups(
                standings,
                req["rankids"],
                bucketed=req["bucketed"],
                limit=req["limit"],
                for_group=0,
            )
        cuts.append(
            {
                "finalists": [
                    {"racerid": f.racer_id, "score": f.score} for f in cut.finalists
                ],
                "concerns": list(cut.concerns),
            }
        )
    return {
        "standings": standings,
        "trophies": trophies,
        "rows": rows,
        "ladders": [[k, v] for k, v in standings.ladders.items()],
        "catalog_counts": [[k, v] for k, v in standings.catalog_counts.items()],
        "catalog": [
            {"name": c.name, "key": c.key, "presentation": c.presentation}
            for c in standings.catalog()
        ],
        "class_keys": [
            [g.id, standings.class_key(g.id)] for g in standings.group_order
        ],
        "rank_keys": [
            [s.id, standings.rank_key(s.id)] for s in standings.subgroup_order
        ],
        "topological_order": [g.id for g in speed_awards._aggregate_order(standings)],
        "awards": _awards_out(awards),
        "reserved_overall": awards.reserved_overall,
        "finalists": cuts,
        "current": _current_out(standings, awards, current_keys),
    }


def _php_current(php: dict) -> list[dict | None]:
    out = []
    for c in php["current"]:
        if c["current"] is None:
            out.append(None)
        else:
            out.append(
                {
                    "key": c["current"]["key"],
                    "awardname": c["current"]["awardname"],
                    "racerid": c["racerid"],
                }
            )
    return out


def _canon(x) -> str:
    return json.dumps(x, ensure_ascii=False)


@dataclass
class Outcome:
    event_id: str
    compared: Counter = field(default_factory=Counter)
    differing: Counter = field(default_factory=Counter)
    deviations: list[str] = field(default_factory=list)
    diffs: dict = field(default_factory=dict)
    tags: set = field(default_factory=set)
    set_aside: str | None = None


def _compare_category(out: Outcome, category: str, php_items, django_items) -> None:
    n = max(len(php_items), len(django_items))
    out.compared[category] += n
    bad = 0
    first = None
    for i in range(n):
        p = php_items[i] if i < len(php_items) else "<missing>"
        d = django_items[i] if i < len(django_items) else "<missing>"
        if _canon(p) != _canon(d):
            bad += 1
            if first is None:
                first = {"index": i, "php": p, "django": d}
    if bad:
        out.differing[category] += bad
        out.diffs[category] = first


def _topological(ev: dict, order: list[int]) -> bool:
    position = {g: i for i, g in enumerate(order)}
    return all(
        position[c["classid"]] < position[x]
        for c in ev["Classes"]
        if c["classid"] in position
        for x in c["constituents"]
        if x in position
    )


def _compare_order(
    out: Outcome, ev: dict, php_order: list[int], django_order: list[int]
) -> None:
    """A different aggregate order is Q8 when PHP's is not topological and
    Django's, of the same aggregates, is."""
    out.compared["topological_order"] += 1
    if php_order == django_order:
        return
    if (
        sorted(php_order) == sorted(django_order)
        and _topological(ev, django_order)
        and not _topological(ev, php_order)
    ):
        out.deviations.append("Q8")
        return
    out.differing["topological_order"] += 1
    out.diffs["topological_order"] = {"php": php_order, "django": django_order}


def _match_awards(out: Outcome, php: dict, dj: dict) -> list[dict | None]:
    """Compares the awards, and returns Django's current-award answers to
    compare with PHP's: those of the awards that equal PHP's, which, for a
    Q7 or Q8 event, are the awards made with PHP's behavior put back."""
    out.compared["awards"] += len(php["awards"])
    if _canon(php["awards"]) == _canon(dj["awards"]):
        return dj["current"]
    keys = [c["key"] for c in php["raw_current"]]
    standings, trophies = dj["standings"], dj["trophies"]
    php_read_a_missing_racer = any(
        w.startswith("awards.inc:") and "Undefined array key" in w
        for w in php["warnings"]
    )
    orders = [None]
    if php["topological_order"] != dj["topological_order"] and "Q8" in out.deviations:
        orders.append(php["topological_order"])
    for order in orders:
        q8 = ["Q8 awards"] if order else []
        ours = _speed_awards_as(standings, trophies, order=order)
        if order and _canon(_awards_out(ours)) == _canon(php["awards"]):
            out.deviations += q8
            return _current_out(standings, ours, keys)
        fixed = _speed_awards_as(
            standings, trophies, awarding=_ModelAwarding, order=order
        )
        as_php = _speed_awards_as(
            standings, trophies, awarding=_PhpQ7Awarding, order=order
        )
        if (
            _canon(_awards_out(fixed)) == _canon(_awards_out(ours))
            and _canon(_awards_out(as_php)) == _canon(php["awards"])
            and php_read_a_missing_racer
        ):
            php_only = [a for a in php["awards"] if a not in dj["awards"]]
            out.deviations += ["Q7"] * len(php_only) + q8
            out.diffs["deviation"] = {
                "php": php_only,
                "django": [a for a in dj["awards"] if a not in php["awards"]],
            }
            return _current_out(standings, as_php, keys)
    out.compared["awards"] -= len(php["awards"])
    _compare_category(out, "awards", php["awards"], dj["awards"])
    return dj["current"]


def compare(ev: dict, php: dict) -> Outcome:
    out = Outcome(ev["id"])
    if "error" in php:
        out.set_aside = php["error"]
        return out
    if any(r["score"] is None for r in php["rows"]):
        out.set_aside = "a PHP row has no score"
        return out
    keys = [c["key"] for c in php["current"]]
    dj = django_side(ev, keys)
    php = dict(php, current=_php_current(php), raw_current=php["current"])
    for category in LISTS:
        _compare_category(out, category, php[category], dj[category])
    _compare_category(
        out, "reserved_overall", [php["reserved_overall"]], [dj["reserved_overall"]]
    )
    _compare_order(out, ev, php["topological_order"], dj["topological_order"])
    django_current = _match_awards(out, php, dj)

    award_keys = {a["key"] for a in php["awards"]}
    split = {"current": ([], []), "current_odd_keys": ([], [])}
    for key, p, d in zip(keys, php["current"], django_current, strict=True):
        php_items, django_items = split[
            "current" if key in award_keys else "current_odd_keys"
        ]
        php_items.append(p)
        django_items.append(d)
    for category, (php_items, django_items) in split.items():
        _compare_category(out, category, php_items, django_items)

    out.tags |= tags(ev, php)
    return out


def tags(ev: dict, php: dict) -> set[str]:
    s = ev["settings"]
    t = set()
    t.add(f"scoring {s['scoring']}")
    t.add("points racing" if s["use-points"] == "1" else "time racing")
    t.add(f"use-subgroups {s['use-subgroups']}")
    t.add(f"one-trophy-per {s['one-trophy-per']}")
    ffc = int(s["full-field-calc"])
    t.add("full-field-calc aggregate" if ffc > 0 else f"full-field-calc {ffc}")
    rungs = [rung for _k, ladder in php["ladders"] for rung in ladder]
    if any(len(r) > 1 for r in rungs):
        t.add("tie in a ladder")
    if any(len(r) > 2 for r in rungs):
        t.add("three-way or wider tie")
    if s["scoring"] == "1" and any(r["base"] == 1 for r in php["rows"]):
        t.add("scoring 1 with a one-heat row")
    if any(r["base"] == 1 for r in php["rows"]) and any(
        r["base"] > 1 for r in php["rows"]
    ):
        t.add("rows with different heat counts")
    classes = ev["Classes"]
    racing = [c for c in classes if not c["constituents"]]
    ranks_per = Counter(r["classid"] for r in ev["Ranks"])
    if len(racing) >= 2 and sum(1 for c in racing if ranks_per[c["classid"]] >= 2) >= 2:
        t.add("several groups with several subgroups")
    round_classes = {r["classid"] for r in ev["Rounds"]}
    aggregates = [c for c in classes if c["constituents"]]
    agg_ids = {c["classid"] for c in aggregates}
    for c in aggregates:
        t.add("aggregate by subgroup" if c["rankids"] else "aggregate by group")
        t.add(
            "racing aggregate"
            if c["classid"] in round_classes
            else "nonracing aggregate"
        )
        if any(x in agg_ids for x in c["constituents"]):
            t.add("aggregate of aggregates")
    chart_rounds = {c["roundid"] for c in ev["RaceChart"]}
    for r in ev["Rounds"]:
        lanes = [c for c in ev["RaceChart"] if c["roundid"] == r["roundid"]]
        if r["roundid"] not in chart_rounds:
            state = "unscheduled"
        elif any(c["finishtime"] is None and c["finishplace"] is None for c in lanes):
            state = "unfinished"
        else:
            state = "finished"
        t.add(f"{'follow-on' if r['round'] > 1 else 'first'} round {state}")
    racers = {r["racerid"]: r for r in ev["RegistrationInfo"]}
    entered = {(r["roundid"], r["racerid"]) for r in ev["Roster"]}
    for c in ev["RaceChart"]:
        if c["finishtime"] is None and c["finishplace"] is None:
            continue
        racer = racers[c["racerid"]]
        if (c["roundid"], c["racerid"]) not in entered:
            t.add("result in a chart but not the roster")
        if not racer["passedinspection"]:
            t.add("result of a racer not passed")
        if racer["exclude"]:
            t.add("result of an excluded racer")
    placed = [racers[r["racerid"]] for r in php["rows"]]
    folded = {}
    for r in placed:
        folded.setdefault(ascii_fold(r["lastname"]), set()).add(r["lastname"])
    if any(len(v) > 1 for v in folded.values()):
        t.add("last names differing only in case")
    if any(not (r["lastname"] + r["firstname"]).isascii() for r in placed):
        t.add("accented names")
    names = Counter(
        (
            r["roundid"],
            r["score"],
            ascii_fold(racers[r["racerid"]]["lastname"]),
            ascii_fold(racers[r["racerid"]]["firstname"]),
        )
        for r in php["rows"]
    )
    if any(n > 1 for n in names.values()):
        t.add("tied rows with the same name")
    if any(c["ntrophies"] >= 0 for c in classes):
        t.add("group ntrophies override")
    if any(r["ntrophies"] >= 0 for r in ev["Ranks"]):
        t.add("subgroup ntrophies override")
    for key in ("n-pack-trophies", "n-den-trophies", "n-rank-trophies"):
        if key in s:
            t.add(f"{key} set")
    if len({c["sortorder"] for c in classes}) < len(classes):
        t.add("groups sharing a sort order")
    return t
