import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from racing.engine.scheduling.generator_tables import GENERATOR_TABLES
from racing.engine.scheduling.generators import scheduler_data

SERVER_DIR = Path(__file__).resolve().parents[3]
CONVERTER = SERVER_DIR / "tools" / "convert_generators.py"
GENERATOR_SOURCES = SERVER_DIR.parent / "website" / "inc" / "generators"
PHP_DUMP = SERVER_DIR / "tests" / "golden" / "engine" / "scheduler-data"
LANE_COUNTS = (2, 3, 4, 5, 6, 8)


def run_converter(*args):
    return subprocess.run(
        [sys.executable, str(CONVERTER), *args],
        capture_output=True,
        text=True,
        cwd=SERVER_DIR,
    )


def php_json_encode(tables):
    """The text json_encode($all_scheduler_data) gives in PHP.

    PHP declares an empty array for each of 1 to 8 lanes and fills six of them.
    """
    everything = {n: tables.get(n, []) for n in range(1, 9)}
    return json.dumps(everything, separators=(",", ":"))


def test_checksum_matches_the_php_dump():
    expected = (PHP_DUMP / "sha256.txt").read_text().split()[0]
    actual = hashlib.sha256(php_json_encode(GENERATOR_TABLES).encode()).hexdigest()
    assert actual == expected


@pytest.mark.parametrize("nlanes", LANE_COUNTS)
def test_each_lane_count_equals_the_php_dump(nlanes):
    php = json.loads((PHP_DUMP / f"lanes-{nlanes}.json").read_text())
    assert GENERATOR_TABLES[nlanes] == {int(k): v for k, v in php.items()}


def test_lane_counts_and_car_counts_are_exactly_covered():
    assert list(GENERATOR_TABLES) == list(LANE_COUNTS)
    for nlanes, table in GENERATOR_TABLES.items():
        assert list(table) == list(range(nlanes, 201))


def test_every_entry_is_well_formed():
    total = 0
    for nlanes, table in GENERATOR_TABLES.items():
        for ncars, entry in table.items():
            total += 1
            where = f"{nlanes} lanes, {ncars} cars"
            generators = entry["generators"]
            sets = entry["permutation_sets"]
            assert all(len(g) == nlanes - 1 for g in generators), where
            assert len(sets) <= len(generators), where
            for permutations in sets:
                for permutation in permutations:
                    assert sorted(permutation) == list(range(ncars)), where
    assert total == 1178


def test_lookup_returns_the_table_entry_or_none():
    assert scheduler_data(2, 2) == {
        "generators": [[1], [1]],
        "permutation_sets": [[[1, 0], [0, 1]], [[0, 1], [1, 0]]],
    }
    assert scheduler_data(8, 200) is GENERATOR_TABLES[8][200]
    for nlanes, ncars in [(1, 5), (7, 20), (9, 20), (4, 3), (4, 201), (0, 0), (2, 1)]:
        assert scheduler_data(nlanes, ncars) is None, (nlanes, ncars)


def test_importing_the_lookup_does_not_load_the_tables():
    code = (
        "import sys\n"
        "from racing.engine.scheduling.generators import scheduler_data\n"
        "table = 'racing.engine.scheduling.generator_tables'\n"
        "assert table not in sys.modules\n"
        "scheduler_data(4, 20)\n"
        "assert table in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=SERVER_DIR
    )
    assert result.returncode == 0, result.stderr


def test_committed_module_is_what_the_converter_makes():
    result = run_converter("--check")
    assert result.returncode == 0, result.stderr


def test_converter_twice_gives_identical_bytes(tmp_path):
    first = tmp_path / "first" / "generator_tables.py"
    second = tmp_path / "second" / "generator_tables.py"
    for out in (first, second):
        out.parent.mkdir()
        assert run_converter("--output", str(out)).returncode == 0
    assert first.read_bytes() == second.read_bytes()
    committed = SERVER_DIR / "racing" / "engine" / "scheduling" / "generator_tables.py"
    assert first.read_bytes() == committed.read_bytes()


def test_converter_names_the_generated_file_in_its_header():
    first_line = (
        (SERVER_DIR / "racing/engine/scheduling/generator_tables.py")
        .read_text()
        .splitlines()[0]
    )
    assert "convert_generators.py" in first_line
    assert "Do not edit" in first_line


@pytest.fixture
def sources(tmp_path):
    copy = tmp_path / "generators"
    shutil.copytree(GENERATOR_SOURCES, copy)
    return copy


def test_check_fails_when_a_table_entry_differs(tmp_path):
    committed = (
        SERVER_DIR / "racing/engine/scheduling/generator_tables.py"
    ).read_text()
    broken = tmp_path / "generator_tables.py"
    broken.write_text(
        committed.replace('"permutation_sets":[[[1,0]', '"permutation_sets":[[[0,1]', 1)
    )
    assert broken.read_text() != committed
    result = run_converter("--check", "--output", str(broken))
    assert result.returncode == 1
    assert "out of date" in result.stderr


def test_check_fails_when_the_module_is_missing(tmp_path):
    assert (
        run_converter("--check", "--output", str(tmp_path / "absent.py")).returncode
        == 1
    )


def test_a_stray_token_is_refused_with_file_and_line(sources, tmp_path):
    path = sources / "2-lanes.inc"
    lines = path.read_text().splitlines()
    lines[2] = lines[2].replace("array(", "arry(", 1)
    path.write_text("\n".join(lines) + "\n")
    result = run_converter(
        "--source", str(sources), "--output", str(tmp_path / "out.py")
    )
    assert result.returncode == 2
    assert f"{path}:3:" in result.stderr


def test_a_float_is_refused(sources, tmp_path):
    path = sources / "3-lanes.inc"
    text = path.read_text()
    assert text.count("make_scheduler_data(3, 3,") == 1
    path.write_text(
        text.replace("make_scheduler_data(3, 3,", "make_scheduler_data(3, 3.5,")
    )
    result = run_converter(
        "--source", str(sources), "--output", str(tmp_path / "out.py")
    )
    assert result.returncode == 2
    assert f"{path}:3:" in result.stderr


def test_an_entry_in_the_wrong_lane_file_is_refused(sources, tmp_path):
    path = sources / "4-lanes.inc"
    path.write_text(
        path.read_text().replace(
            "make_scheduler_data(4, 4,", "make_scheduler_data(5, 4,", 1
        )
    )
    result = run_converter(
        "--source", str(sources), "--output", str(tmp_path / "out.py")
    )
    assert result.returncode == 2
    assert "in a 4-lane file" in result.stderr


def test_comment_lines_with_parentheses_are_skipped(sources, tmp_path):
    out = tmp_path / "out.py"
    assert run_converter("--source", str(sources), "--output", str(out)).returncode == 0
    assert "// Only 1 permutation(s)" in (sources / "8-lanes.inc").read_text()
    assert "permutation(s)" not in out.read_text()
