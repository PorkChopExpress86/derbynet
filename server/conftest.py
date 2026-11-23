import os

import pytest

# Each browser test, fixtures included, fails after this many seconds, so a
# hang ends the run. #247's probe awaited axe on a paused Playwright clock;
# `page.evaluate` has no timeout, and the run sat for about 10 hours. The
# slowest browser test took 31 s with 8 workers (test_smoke.py, phone), so
# this leaves about ten times that for CI's smaller runners and
# tools/pytest-under-load.sh. `--timeout N` or PYTEST_TIMEOUT sets every
# test's bound instead, and `--timeout 0` turns it off (#247 retro).
BROWSER_TIMEOUT = 300


def pytest_report_header(config):
    """The folder and the commit atop every log: #324's hand-off logs named
    neither, and the set ran again (tests/test_report_header.py). The
    commit is the rootdir's, wherever pytest started (#340 review)."""
    import subprocess

    def git(*args):
        try:
            found = subprocess.run(
                ["git", *args], cwd=config.rootpath, capture_output=True, text=True
            )
        except OSError:  # no git, as in an image without it
            return None, False
        return (found.stdout.strip() if found.returncode == 0 else None), True

    head, has_git = git("rev-parse", "HEAD")
    if head is None:
        head = "unknown (not a git checkout)" if has_git else "unknown (no git)"
    elif git("status", "--porcelain", "--untracked-files=no")[0]:
        head += " with uncommitted changes"
    return [f"cwd: {os.getcwd()}", f"head: {head}", *_load_run_lines(config)]


def _load_run_lines(config):
    """A line naming the run that holds tools/pytest-under-load.sh's lock,
    or none. #316's hand-off ran beside #317's 25-count run at a load
    average of 35, failed a test that passes alone, and ran again (#316
    retro). The script's own pytest, which carries DERBYNET_LOAD_LOCK_HELD,
    names nothing. It only probes: it never waits for, keeps or creates the
    lock (tests/test_report_header.py). A run that runs no test
    (`--collect-only`, `--setup-plan`, `--setup-only`) names nothing: the
    holder's arguments can hold node ids, and tests read `--collect-only
    -q` output for lines with `::` in them (#354 review)."""
    import fcntl

    if os.environ.get("DERBYNET_LOAD_LOCK_HELD"):
        return []
    option = getattr(config, "option", None)
    if any(
        getattr(option, name, False)
        for name in ("collectonly", "setuponly")  # --setup-plan sets setuponly
    ):
        return []
    path = os.environ.get("DERBYNET_LOAD_LOCK")
    if not path:
        home = os.environ.get("HOME")
        if not home:
            return []
        cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
        path = os.path.join(cache, "derbynet", "load-run.lock")
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:  # no load run has run here, as on CI
        return []
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            lines = os.read(fd, 4096).decode(errors="replace").splitlines()
            holder = "; ".join(lines) or "no details yet"
            return [
                f"load run: tools/pytest-under-load.sh holds {path} ({holder}); "
                "results beside it are not clean"
            ]
        return []
    finally:
        os.close(fd)  # which drops the probe's own shared lock


def pytest_terminal_summary(terminalreporter, config):
    """The holder again at the end, since a load run may start mid-run."""
    for line in _load_run_lines(config):
        terminalreporter.write_line(line, yellow=True, bold=True)


def pytest_configure():
    """SIGINT and SIGQUIT get a terminal run's handling, though pytest may
    have started ignoring them, as a `&` job of a non-interactive bash does
    under pytest-under-load.sh and hold-load-lock.sh: the commands a test
    starts inherit an ignore, and bash cannot trap one (#311, #407;
    tests/test_signals_at_default.py)."""
    import signal

    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGQUIT, signal.SIG_DFL)

    from django.conf import settings

    settings.LEGACY_SESSION_WRITES_RAISE = True
    # A browser test's re-seed gives the roles new salts, so its login and
    # its first page hashed again with PBKDF2, slowly enough under load to
    # time the login out (#430, accounts/tests/test_login_cost.py).
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def _data_dir(settings, tmp_path_factory):
    """Each test's data folder (event folders, photo locks) is its own, never
    `server/var/data`. A full run wrote some 250 folders and files there,
    which every xdist worker and a dev server shared: in #212 a file
    comparison saw another worker's upload (#212 retro). A test that needs
    the folder at a known path sets `settings.DATA_DIR` itself. It sits
    beside `tmp_path`, not in it, so a test that lists or fills `tmp_path`
    finds only its own files."""
    settings.DATA_DIR = tmp_path_factory.mktemp("derbynet-data")


ID_OFFSET_STEP = 1000


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Each table's ids start at their own multiple of ID_OFFSET_STEP, never
    at 1, on SQLite as on Postgres. In a fresh SQLite test database a row's
    id equalled its position, so an export that wrote a database id where
    the importer reads a position passed every SQLite test and failed only
    on Postgres (#221's RaceInfo ids, #221 retro). A start of its own per
    table also fails a test that passes one table's id as another's."""
    from django.apps import apps
    from django.db import connection, models

    tables = sorted(
        (model._meta.db_table, model._meta.pk.column)
        for model in apps.get_models(include_auto_created=True)
        if model._meta.managed and isinstance(model._meta.pk, models.AutoField)
    )
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        for n, (table, column) in enumerate(tables, start=1):
            start = n * ID_OFFSET_STEP
            if connection.vendor == "sqlite":
                cursor.execute("DELETE FROM sqlite_sequence WHERE name = %s", [table])
                cursor.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES (%s, %s)",
                    [table, start],
                )
            else:
                cursor.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, %s), %s)",
                    [table, column, start],
                )


def pytest_collection_modifyitems(items):
    from racing.engine.tests.golden import reads_golden_fixtures

    golden = {}
    for item in items:
        module = getattr(item, "module", None)
        if module is None:
            continue
        if module.__name__ not in golden:
            golden[module.__name__] = reads_golden_fixtures(module)
        if golden[module.__name__]:
            item.add_marker(pytest.mark.golden)


def pytest_collection_finish(session):
    """After every conftest's pytest_collection_modifyitems, so the browser
    marker that ui/tests/browser/conftest.py adds is on."""
    if (
        session.config.getoption("timeout") is not None
        or "PYTEST_TIMEOUT" in os.environ
    ):
        return
    for item in session.items:
        bounded = item.get_closest_marker("timeout")
        if item.get_closest_marker("browser") and not bounded:
            item.add_marker(pytest.mark.timeout(BROWSER_TIMEOUT))
