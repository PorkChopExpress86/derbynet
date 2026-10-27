"""Differential test of the Django standings engine against real PHP.

    uv run python -m tools.standings_differential [IMAGE] [options]

Run from server/. IMAGE is a Docker image with `php84` and the DerbyNet
website at /var/www/html; it defaults to `derbynet-local`. The command
generates seeded random events (`generate.py`), runs PHP on them in
throwaway `docker run --rm` containers (`harness.php`), runs the Django
engine on the same events (`compare.py`), and prints the counts. It exits 1
when any comparison differs other than by the deliberate differences that
`compare.py` counts apart (Q7, Q8, Q22), when a seeded fault goes unreported
(`--controls`), or when PHP could not run an event. A `current_award_key`
that makes PHP's `award.current` throw is recorded as naming no award.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.standings_differential.compare import CATEGORIES, compare
from tools.standings_differential.controls import CONTROLS, fault
from tools.standings_differential.generate import BATCHES, random_event

HERE = Path(__file__).resolve().parent


def run_php(image: str, name: str, workdir: Path, chunk: str) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{HERE}:/harness:ro",
            "-v",
            f"{workdir}:/work",
            "-w",
            "/var/www/html",
            "--entrypoint",
            "php84",
            image,
            "/harness/harness.php",
            f"/work/{chunk}.in.jsonl",
            f"/work/{chunk}.out.jsonl",
        ],
        check=True,
    )


def plan(args) -> list[tuple[str, str | None, list[int]]]:
    chunks = []
    seeds = list(range(args.seed, args.seed + args.events))
    for i in range(0, len(seeds), args.chunk):
        chunks.append((f"random-{i // args.chunk}", None, seeds[i : i + args.chunk]))
    for batch in args.batches:
        seeds = list(range(args.seed, args.seed + args.per_batch))
        chunks.append((batch, batch, seeds))
    return chunks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("image", nargs="?", default="derbynet-local")
    parser.add_argument("--events", type=int, default=2000, help="random events")
    parser.add_argument(
        "--per-batch", type=int, default=200, help="events per targeted batch"
    )
    parser.add_argument(
        "--batches", nargs="*", default=list(BATCHES), choices=list(BATCHES)
    )
    parser.add_argument("--seed", type=int, default=0, help="first seed")
    parser.add_argument(
        "--chunk", type=int, default=250, help="random events per container"
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--name-prefix", default="standings-differential")
    parser.add_argument(
        "--keep", type=Path, help="keep events and PHP results in this directory"
    )
    parser.add_argument("--show", type=int, default=5, help="differing events to print")
    parser.add_argument(
        "--controls",
        action="store_true",
        help="also run each seeded engine fault and check the comparison reports it",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="standings-differential-") as tmp:
        workdir = (args.keep or Path(tmp)).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        chunks = plan(args)
        events: dict[str, dict] = {}
        for chunk, batch, seeds in chunks:
            with open(workdir / f"{chunk}.in.jsonl", "w") as f:
                for seed in seeds:
                    ev = random_event(seed, batch)
                    events[ev["id"]] = ev
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        generated = time.monotonic()
        with ThreadPoolExecutor(args.jobs) as pool:
            list(
                pool.map(
                    lambda c: run_php(
                        args.image,
                        f"{args.name_prefix}-{c[0]}-{os.getpid()}",
                        workdir,
                        c[0],
                    ),
                    chunks,
                )
            )
        php_done = time.monotonic()
        results = []
        for chunk, _batch, _seeds in chunks:
            with open(workdir / f"{chunk}.out.jsonl") as f:
                results += [(chunk, json.loads(line)) for line in f]
        outcomes = [(chunk, compare(events[php["id"]], php)) for chunk, php in results]
        compared = time.monotonic()
    status = report(args, outcomes, started, generated, php_done, compared)
    if args.controls:
        status |= run_controls(events, results)
        print(f"\ncontrols {time.monotonic() - compared:.1f}s")
    return status


def run_controls(events, results) -> int:
    print("\nseeded faults (events whose category differs):")
    missed = 0
    for name, (category, _patch) in CONTROLS.items():
        with fault(name):
            caught = sum(
                1
                for _c, php in results
                if compare(events[php["id"]], php).differing[category]
            )
        missed += caught == 0
        print(f"  {'caught' if caught else 'MISSED'}  {name:40} {category:18} {caught}")
    return 1 if missed else 0


def report(args, outcomes, started, generated, php_done, compared) -> int:
    by_batch: dict[str, Counter] = {}
    compared_items, differing_items = Counter(), Counter()
    tag_events = Counter()
    deviations, occurrences = Counter(), Counter()
    failing, set_aside = [], []
    for chunk, out in outcomes:
        batch = "random" if chunk.startswith("random-") else chunk
        b = by_batch.setdefault(batch, Counter())
        b["events"] += 1
        if out.set_aside:
            set_aside.append(out)
            b["set aside"] += 1
            continue
        compared_items.update(out.compared)
        differing_items.update(out.differing)
        tag_events.update(out.tags)
        occurrences.update(out.deviations)
        for d in set(out.deviations):
            deviations[d] += 1
            b[d] += 1
        if out.differing:
            failing.append(out)
            b["differing"] += 1

    print(
        f"{len(outcomes)} events; generate {generated - started:.1f}s, "
        f"PHP {php_done - generated:.1f}s, compare {compared - php_done:.1f}s, "
        f"total {compared - started:.1f}s"
    )
    print("\nper batch:")
    for batch, c in by_batch.items():
        print(f"  {batch:28} " + ", ".join(f"{k} {v}" for k, v in c.items()))
    print("\nitems compared (differing) per category:")
    for category in CATEGORIES:
        print(
            f"  {category:20} {compared_items[category]:8} "
            f"({differing_items[category]})"
        )
    print("\nevents exercising each feature:")
    for tag, n in sorted(tag_events.items()):
        print(f"  {tag:45} {n}")
    print("\ndeliberate deviations:" + ("" if deviations else " none"))
    for d, n in sorted(deviations.items()):
        print(f"  {d:45} {n} events, {occurrences[d]} items")
    if set_aside:
        print(f"\nset aside ({len(set_aside)} events PHP could not run):")
        for reason, n in Counter(o.set_aside for o in set_aside).most_common():
            print(f"  {n:5}  {reason}")
    if failing:
        print(f"\n{len(failing)} events differ:")
        for out in failing[: args.show]:
            print(
                f"  {out.event_id}: {json.dumps(out.diffs, ensure_ascii=False)[:2000]}"
            )
    return 1 if failing or set_aside else 0


if __name__ == "__main__":
    sys.exit(main())
