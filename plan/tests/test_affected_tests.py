"""plan/bin/affected-tests against a small stand-in of the repository, so each
mapping rule has a test that fails when the rule is removed, and against the
real tree, so every page file maps to a test without the fallback.

Run: python3 -I -m unittest discover -s plan/tests
"""
import ast
import contextlib
import importlib.machinery
import importlib.util
import io
import itertools
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

REPO = Path(__file__).resolve().parents[2]
AFFECTED = REPO / 'plan' / 'bin' / 'affected-tests'

# The stand-in's files, with just the text the rules read.
TREE = {
    'server/tools/source-checks.sh': 'exec pytest \\\n  ui/tests/test_photo_urls.py \\\n',
    'server/tools/pytest-under-load.sh': '#! /bin/bash\n',
    'server/tests/test_pytest_under_load.py': 'SCRIPT = "tools/pytest-under-load.sh"\n',
    'server/conftest.py': 'from racing.engine.tests.golden import reads\n',
    'server/racing/engine/tests/golden.py': 'def reads(): pass\n',
    'server/racing/engine/tests/test_tables.py': 'DATA = "tables.json"\n',
    'server/racing/engine/tests/data/tables.json': '{}\n',
    'server/racing/engine/scheduling.py': 'def plan(): pass\n',
    'server/racing/models.py': 'from django.db import models\n',
    'server/racing/services/heats.py': 'def advance(): pass\n',
    'server/racing/services/tests/test_heats.py': 'from racing.services import heats\n',
    'server/racing/tests/test_round_scenarios.py': 'SCRIPT = "test-rounds.sh"\n',
    'server/racing/tests/test_finalists.py': '',
    'server/roster/tests/test_group_scenarios.py': 'SCRIPT = "test-groups.sh"\n',
    'server/legacy_api/handlers/groups.py': 'def class_add(ctx): pass\n',
    'server/legacy_api/tests/test_groups_api.py': '',
    'server/legacy_api/tests/test_groups_parity.py': '',
    'server/legacy_api/tests/test_registry.py': '',
    'server/legacy_api/tests/test_event_leak.py': '',
    'server/core/tests/factories.py': 'class EventFactory: pass\n',
    'server/core/tests/populated.py': 'from core.tests.factories import EventFactory\n',
    'server/core/tests/test_populated.py': 'from core.tests import populated\n',
    'server/ui/tests/db.py': 'from core.tests.populated import EventFactory\n',
    'server/ui/tests/test_through_db.py': 'from .db import EventFactory\n',
    'server/awards/editing.py': 'def edit(award):\n    return 1\n',
    'server/awards/tests/test_editing.py': '',
    'server/ui/tests/conftest.py': 'from core.tests.factories import EventFactory\n',
    'server/ui/tests/test_navigation.py': '',
    'server/ui/tests/test_every_view.py': '',
    'server/ui/tests/test_query_growth.py': '',
    'server/ui/tests/test_photo_urls.py': '',
    'server/ui/tests/test_dashboard.py': '',
    'server/ui/tests/test_kiosk_pages.py': '',
    'server/ui/tests/test_timer_pages.py': '',
    'server/ui/tests/test_settings_bounds.py': 'PHP = "website/settings.php"\n',
    'server/ui/tests/test_screens_tool.py': (
        'import pytest\n\npytestmark = pytest.mark.browser\nWIDE = "scrollWidth"\n'),
    'server/ui/tests/test_mixed.py': (
        'import pytest\n\n\n@pytest.mark.browser\ndef test_a(): pass\n'),
    'server/ui/tests/browser/conftest.py': '',
    'server/ui/tests/browser/test_kiosk_ondeck_labels.py': (
        'def test_fits(page):\n    page.evaluate("e => e.scrollWidth")\n'),
    'server/ui/tests/browser/test_dashboard.py': (
        'from racing.services.heats import advance\n\n'
        'def test_poll(page):\n    page.route("**/action.php", hold)\n'),
    'server/ui/tests/browser/test_table_columns.py': 'SCRIPT = "kiosk-ondeck.js"\n',
    'server/ui/tests/browser/test_timer.py': '',
    'server/ui/dashboard/views.py': 'def page(request): pass\n',
    'server/ui/middleware.py': 'def middleware(get): pass\n',
    'server/ui/templates/ui/base.html': "{% load static %}{% static 'ui/app.css' %}\n",
    'server/ui/templates/ui/dashboard/page.html': '{% extends "ui/base.html" %}\n',
    'server/ui/templates/kiosk/base.html': "{% static 'ui/kiosk.css' %}\n",
    'server/ui/templates/kiosk/pages/ondeck.html': (
        "{% extends \"kiosk/base.html\" %}{% static 'ui/kiosk-ondeck.js' %}\n"),
    'server/ui/static/ui/app.css': '',
    'server/ui/static/ui/kiosk.css': '',
    'server/ui/static/ui/kiosk-ondeck.js': '',
    'server/manage.py': '',
    'server/core/database_scripts.py': 'def migrate(): pass\n',
    'server/legacy_api/tests/test_database_execute.py': (
        'from unittest import mock\n\n\ndef test_a():\n'
        '    mock.patch("core.database_scripts.call_command")\n'),
    'server/legacy_api/handlers/race_control.py': 'def poll(ctx): pass\n',
    'server/ui/tests/test_dashboard_poll.py': (
        'def test_a(monkeypatch):\n'
        '    monkeypatch.setattr("legacy_api.handlers.race_control.poll", None)\n'
        '    print("racing.services.heats")\n'),
    'server/core/database_scripts_gone.py': 'X = 1\n',
    'server/core/tests/test_delattr.py': (
        'def test_a(monkeypatch):\n'
        '    monkeypatch.delattr("core.database_scripts_gone.X")\n'),
    'server/ui/management/commands/build_css.py': 'class Command: pass\n',
    'server/ui/tests/test_build_css_command.py': 'call_command("build_css", "--check")\n',
    'server/tests/test_management_commands.py': 'APPS = []\n',
    'server/tools/standings_differential/__main__.py': 'from .compare import compare\n',
    'server/tools/standings_differential/compare.py': 'def compare(): pass\n',
    'server/racing/engine/tests/test_differential_controls.py': (
        'from tools.standings_differential.compare import compare\n'),
    'server/racing/engine/tests/test_recorded.py': (
        '"""Recorded by `tools/standings_differential` from PHP."""\n'),
    'server/tools/time_standings.py': 'def main(): pass\n',
    'testing/test-groups.sh': '',
    'website/settings.php': '',
    'website/css/ondeck.css': '',
}


def run(*files, tree=None, root=None):
    """Runs the tool on `files` with --stdin over the stand-in, or `root`."""
    with tempfile.TemporaryDirectory() as tmp:
        if root is None:
            root = Path(tmp)
            for rel, text in (tree or TREE).items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        result = subprocess.run(
            [sys.executable, '-I', str(AFFECTED), '--stdin', '--root', str(root)],
            input=''.join(f'{f}\n' for f in files), capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


def section(out, path):
    """The lines the output prints under one changed file."""
    lines = out.split('\n\n', 1)[1].splitlines()
    start = lines.index(path)
    end = next((i for i in range(start + 1, len(lines))
                if not lines[i].startswith(' ')), len(lines))
    return '\n'.join(lines[start:end])


def commands(out):
    return out.split('Run from server/', 1)[1]


def before_hand_off(out):
    """The lines printed to run before the first hand-off run, ahead of the
    commands."""
    if 'Before the first hand-off run' not in out:
        return ''
    return out.split('Before the first hand-off run', 1)[1].split('Run from server/', 1)[0]


def docker_command(out):
    """The printed line that builds the image and runs checks.sh."""
    found = [line.strip() for line in commands(out).splitlines() if 'docker build' in line]
    if len(found) != 1:
        raise AssertionError(out)
    return found[0]


def stub(path, body):
    path.write_text(f'#!/bin/bash\n{body}\n')
    path.chmod(0o755)


class Everything(unittest.TestCase):
    def test_pyproject_leaves_the_full_suites_to_ci(self):
        """D85: an "everything" diff runs its targeted tests locally, and CI's
        full run on both databases is the check (#284's 48-60 min serial run)."""
        out = run('server/pyproject.toml',
                  'server/ui/tests/browser/test_kiosk_ondeck_labels.py')
        self.assertIn('[everything]', out)
        self.assertIn('CI runs the full suites on both databases (D85)', out)
        cmds = commands(out)
        self.assertIn('  uv run pytest -n auto -m browser '
                      'ui/tests/browser/test_kiosk_ondeck_labels.py\n', cmds)
        self.assertNotIn('  uv run pytest -n auto\n', cmds)
        self.assertNotIn('-m browser ui\n', cmds)
        self.assertNotIn('tools/pytest-postgres.sh', cmds)
        self.assertNotIn('ui/tests/test_screens_tool.py', cmds)

    def test_the_settings_still_need_postgres_for_the_targeted_tests(self):
        """D85 keeps a local Postgres run for database code, and the settings
        hold the database settings; a conftest or pyproject.toml does not."""
        out = run('server/derbynet/settings.py',
                  'server/ui/tests/browser/test_kiosk_ondeck_labels.py')
        self.assertIn('server/derbynet/settings.py: the database settings', out)
        self.assertIn('  tools/pytest-postgres.sh -n auto -m browser '
                      'ui/tests/browser/test_kiosk_ondeck_labels.py', commands(out))
        out = run('server/ui/tests/browser/conftest.py')
        self.assertIn('Postgres: not needed', out)

    def test_a_conftest_leaves_the_full_run_to_ci(self):
        out = run('server/ui/tests/browser/conftest.py')
        self.assertIn('[everything] a shared conftest', out)

    def test_a_workflow_leaves_the_full_run_to_ci(self):
        out = run('.github/workflows/server.yml')
        self.assertIn('[everything] a CI workflow', out)


class Docs(unittest.TestCase):
    def test_markdown_maps_to_no_target_and_says_why(self):
        out = run('plan/README.md')
        self.assertIn('[docs] docs only', out)
        self.assertNotIn('[fallback]', out)
        self.assertIn('(no pytest target', out)


class Guide(unittest.TestCase):
    """A guide in docs/ renders through the docs build; #61's fix round
    left a fence open that only CI's build could find (#61 retro)."""

    def test_a_guide_maps_to_the_docs_build_and_its_check(self):
        for path in ('docs/install-docker.md', 'docs/img/live-streaming/a.jpg'):
            with self.subTest(path=path):
                out = run(path, root=REPO)
                self.assertIn('[guide] a guide served under /docs/', section(out, path))
                self.assertNotIn('[docs]', out)
                self.assertNotIn('[fallback]', out)
                cmds = commands(out)
                self.assertIn('ui/tests/test_docs.py', cmds)
                self.assertIn('tests/test_check_paths.py', cmds)
                self.assertIn('-m browser ui/tests/browser/test_docs.py', cmds)
                self.assertIn('  bash docs/adr/check-paths.sh --docs ../docs\n', cmds)

    def test_markdown_elsewhere_in_docs_is_still_docs_only(self):
        for path in ('docs/obsolete/old.md', 'docs/Quick Reference.fodt'):
            with self.subTest(path=path):
                out = run(path)
                self.assertIn('[docs] docs only', out)
                self.assertNotIn('[guide]', out)


class Website(unittest.TestCase):
    def test_a_diff_with_no_target_leaves_it_to_the_checks(self):
        out = run('plan/README.md')
        self.assertIn('(no pytest target: the checks below cover the diff)', commands(out))
        self.assertNotIn('D85', out)

    def test_php_is_frozen_and_maps_to_the_tests_that_read_it(self):
        out = run('website/settings.php')
        self.assertIn('[website] the PHP server is frozen', section(out, 'website/settings.php'))
        self.assertIn('ui/tests/test_settings_bounds.py', commands(out))


    def test_a_generator_table_runs_its_conversion_check(self):
        out = run('website/inc/generators/four-lanes.inc')
        self.assertIn('racing/engine/tests/test_generator_tables.py',
                      section(out, 'website/inc/generators/four-lanes.inc'))
        self.assertIn('tools/convert_generators.py --check', commands(out))

    def test_php_no_test_names_says_so(self):
        out = run('website/index.php')
        self.assertIn('No test names it', out)
        self.assertNotIn('[fallback]', out)


class PlanTools(unittest.TestCase):
    def test_a_plan_tool_runs_the_server_tests_that_name_it(self):
        tree = {**TREE, 'plan/bin/tool': '',
                'server/tests/test_tool_sweep.py': 'TOOL = "plan/bin/tool"\n'}
        out = run('plan/bin/tool', tree=tree)
        self.assertIn('tests/test_tool_sweep.py', section(out, 'plan/bin/tool'))
        self.assertIn('uv run pytest -n auto tests/test_tool_sweep.py', commands(out))
        out = run('plan/bin/other', tree={**tree, 'plan/bin/other': ''})
        self.assertNotIn('test_tool_sweep', out)

    def test_a_plan_tool_runs_plan_tests_through_the_source_checks(self):
        """tools/source-checks.sh runs plan/tests, so a line of their own ran
        them twice at each hand-off (#338)."""
        out = run('plan/bin/pr-merge')
        self.assertIn('[plan-tools]', out)
        self.assertNotIn('unittest discover', commands(out))
        self.assertIn('  tools/source-checks.sh\n', commands(out))

    def test_the_pre_push_hook_runs_plan_tests(self):
        self.assertIn('[plan-tools]', run('.githooks/pre-push'))


class TimerBuild(unittest.TestCase):
    def test_the_timer_source_maps_to_the_timer_tests(self):
        out = run('timer/src/Timer.java')
        self.assertIn('[timer-build]', out)
        self.assertIn('ui/tests/browser/test_timer.py', commands(out))


class Testing(unittest.TestCase):
    def test_a_shell_suite_maps_to_the_replay_that_names_it(self):
        out = run('testing/test-groups.sh')
        self.assertIn('[testing]', out)
        self.assertIn('roster/tests/test_group_scenarios.py', commands(out))


class TestFile(unittest.TestCase):
    def test_a_browser_test_runs_itself_under_the_browser_marker(self):
        out = run('server/ui/tests/browser/test_kiosk_ondeck_labels.py')
        self.assertIn('[test-file]', out)
        self.assertIn('uv run pytest -n auto -m browser '
                      'ui/tests/browser/test_kiosk_ondeck_labels.py', commands(out))
        self.assertNotIn('uv run pytest -n auto ui/', commands(out))

    def test_a_layout_test_repeats_5_per_font(self):
        out = run('server/ui/tests/browser/test_kiosk_ondeck_labels.py')
        self.assertIn('test_kiosk_ondeck_labels.py: 5 repeats, deterministic', out)
        self.assertIn('DERBYNET_BROWSER_FONT="DejaVu Sans" tools/pytest-under-load.sh '
                      '-m browser -n 4 --count 5', out)

    def test_a_test_that_holds_a_request_repeats_25(self):
        out = run('server/ui/tests/browser/test_dashboard.py')
        self.assertIn('test_dashboard.py: 25 repeats, involves timing', out)
        self.assertNotIn('DERBYNET_BROWSER_FONT', out)

    def test_a_marked_file_outside_browser_runs_under_the_marker(self):
        out = run('server/ui/tests/test_screens_tool.py')
        self.assertIn('-m browser ui/tests/test_screens_tool.py', commands(out))
        self.assertNotIn('uv run pytest -n auto ui/tests/test_screens_tool.py',
                         commands(out))

    def test_a_file_with_one_marked_test_runs_both_ways(self):
        out = commands(run('server/ui/tests/test_mixed.py'))
        self.assertIn('uv run pytest -n auto ui/tests/test_mixed.py', out)
        self.assertIn('uv run pytest -n auto -m browser ui/tests/test_mixed.py', out)

    def test_a_deleted_test_file_says_so(self):
        out = run('server/ui/tests/test_gone.py')
        self.assertIn('deleted: nothing of its own to run', out)
        self.assertNotIn('[fallback]', out)


class TestHelper(unittest.TestCase):
    def test_populated_maps_to_its_importers_and_needs_postgres(self):
        out = run('server/core/tests/populated.py')
        self.assertIn('[test-helper]', out)
        self.assertIn('core/tests/test_populated.py', commands(out))
        self.assertIn('Postgres: needed', out)

    def test_a_test_that_imports_it_through_a_helper_runs(self):
        out = run('server/core/tests/populated.py')
        self.assertIn('ui/tests/test_through_db.py', commands(out))

    def test_a_helper_a_conftest_imports_runs_that_folder(self):
        out = run('server/core/tests/factories.py')
        self.assertIn('-m browser ui/tests', commands(out))

    def test_a_helper_the_root_conftest_imports_leaves_the_full_run_to_ci(self):
        out = run('server/racing/engine/tests/golden.py')
        self.assertIn('the root conftest imports it', out)
        self.assertIn('CI runs the full suites on both databases (D85)', out)
        self.assertIn('  uv run pytest -n auto racing/engine/tests', commands(out))
        self.assertNotIn('  uv run pytest -n auto\n', commands(out))


class TestData(unittest.TestCase):
    def test_a_fixture_maps_to_the_test_that_names_it(self):
        out = run('server/racing/engine/tests/data/tables.json')
        self.assertIn('[test-data]', out)
        self.assertIn('  uv run pytest -n auto racing/engine/tests', commands(out))


class Model(unittest.TestCase):
    def test_a_model_runs_core_and_the_registry_checks_on_postgres(self):
        out = run('server/racing/models.py')
        self.assertIn('[model]', out)
        self.assertIn('core/tests', commands(out))
        self.assertIn('legacy_api/tests/test_event_leak.py', commands(out))
        self.assertIn('makemigrations --check', commands(out))
        self.assertIn('server/racing/models.py: a model or a migration', out)


class Service(unittest.TestCase):
    def test_a_service_runs_the_replay_suites_on_postgres(self):
        out = run('server/racing/services/heats.py')
        self.assertIn('[service]', out)
        self.assertIn('roster/tests/test_group_scenarios.py', commands(out))
        self.assertIn('server/racing/services/heats.py: a service', out)

    def test_a_service_runs_the_event_leak_check(self):
        out = run('server/racing/services/heats.py')
        self.assertIn('legacy_api/tests/test_event_leak.py',
                      section(out, 'server/racing/services/heats.py'))


class Handler(unittest.TestCase):
    def test_a_handler_runs_the_registry_and_its_named_tests(self):
        out = run('server/legacy_api/handlers/groups.py')
        self.assertIn('[handler]', out)
        self.assertIn('legacy_api/tests/test_groups_api.py', commands(out))
        self.assertIn('legacy_api/tests/test_registry.py', commands(out))
        self.assertIn('racing/tests/test_round_scenarios.py', commands(out))


class UiPython(unittest.TestCase):
    def test_a_view_runs_the_tests_named_after_it_and_the_page_wide_checks(self):
        out = run('server/ui/dashboard/views.py')
        self.assertIn('[ui-python]', out)
        self.assertIn('ui/tests/test_dashboard.py', commands(out))
        self.assertIn('-m browser ui/tests/browser/test_dashboard.py', commands(out))
        self.assertIn('ui/tests/test_every_view.py', commands(out))


class AppPython(unittest.TestCase):
    def test_app_code_runs_its_nearest_tests_folder(self):
        out = run('server/racing/engine/scheduling.py')
        self.assertIn('[app-python]', out)
        self.assertIn('racing/engine/tests', commands(out))


class Importers(unittest.TestCase):
    def test_a_test_elsewhere_that_imports_it_runs(self):
        out = run('server/racing/services/heats.py')
        importers = section(out, 'server/racing/services/heats.py').split('[importers]')[1]
        self.assertIn('ui/tests/browser/test_dashboard.py', importers)

    def test_a_test_this_python_cannot_parse_still_counts_its_imports(self):
        """The server is 3.14 and plan-checks' python3 was 3.12, which reads
        `except A, B:` as a SyntaxError; such a test keeps its importers."""
        tree = {**TREE, 'server/ui/tests/test_unparsed.py':
                'from racing.services import heats\n'
                'mock.patch("racing.engine.scheduling.plan")\n'
                'def broken(:\n    pass\n'}
        heats = section(run('server/racing/services/heats.py', tree=tree),
                        'server/racing/services/heats.py')
        self.assertIn('ui/tests/test_unparsed.py', heats)
        plan = section(run('server/racing/engine/scheduling.py', tree=tree),
                       'server/racing/engine/scheduling.py')
        self.assertIn('ui/tests/test_unparsed.py', plan)


class DbCode(unittest.TestCase):
    def test_a_module_this_python_cannot_parse_counts_as_database_code(self):
        """plan-checks on 3.12 read awards/editing.py's 3.14 `except A, B:`
        as a SyntaxError and said Postgres was not needed."""
        tree = {**TREE, 'server/awards/editing.py': 'def broken(:\n    pass\n'}
        out = run('server/awards/editing.py', tree=tree)
        self.assertIn('cannot be parsed by this Python, so counted as database code', out)
        self.assertNotIn('Postgres: not needed', out)

    def test_a_queryset_needs_postgres(self):
        tree = {**TREE, 'server/awards/editing.py': 'Award.objects.filter(id=1)\n'}
        out = run('server/awards/editing.py', tree=tree)
        self.assertIn("server/awards/editing.py: database code ('.objects')", out)

    def test_a_queryset_method_counts_only_where_django_is_imported(self):
        """tools/operand_mutants.py's docstring and regex name `.filter(`
        and it imports no Django: no Postgres (#358 review). A view that
        imports Django and filters a queryset it is given still needs it."""
        tool = ('"""Lists `.filter(x => ...)` mutants."""\nimport re\n\n'
                'FILTER = re.compile(r"\\.filter\\(\\s*")\nrows = {}.values()\n')
        view = 'from django.shortcuts import render\n\n\ndef v(qs):\n    return qs.filter(a=1)\n'
        tree = {**TREE, 'server/tools/lister.py': tool, 'server/ui/listing.py': view}
        self.assertIn('Postgres: not needed', run('server/tools/lister.py', tree=tree))
        self.assertIn("server/ui/listing.py: database code ('.filter(')",
                      run('server/ui/listing.py', tree=tree))
        # The queryset rule adds to the others, and a test still needs raw SQL.
        tree['server/ui/rows.py'] = 'from django.shortcuts import render\nAward.objects.all()\n'
        tree['server/ui/tests/test_rows.py'] = ('from django.test import TestCase\n'
                                                'Award.objects.filter(a=1)\n')
        self.assertIn("server/ui/rows.py: database code ('.objects')",
                      run('server/ui/rows.py', tree=tree))
        self.assertIn('Postgres: not needed', run('server/ui/tests/test_rows.py', tree=tree))

    def test_css_needs_no_postgres(self):
        self.assertIn('Postgres: not needed', run('server/ui/static/ui/kiosk.css'))

    def test_a_module_that_imports_models_is_database_code(self):
        for text, name in (('from awards.models import Award\n', 'awards.models'),
                           ('from .models import Award\n', 'models'),
                           ('from . import models\n', 'models'),
                           ('import roster.models\n', 'roster.models'),
                           ('from django.db import transaction\n', 'django.db')):
            with self.subTest(text=text):
                tree = {**TREE, 'server/awards/editing.py': text}
                out = run('server/awards/editing.py', tree=tree)
                self.assertIn('server/awards/editing.py: database code, as a whole module '
                              f'(imports {name})', out)

    def test_an_orm_write_call_is_database_code(self):
        for call, attr in (('award.save()', 'save'), ('award.save(update_fields=["a"])', 'save'),
                           ('Award.create(name="a")', 'create'), ('qs.update(slug="a")', 'update'),
                           ('award.delete()', 'delete'), ('Vote.bulk_create(votes)', 'bulk_create'),
                           ('Vote.bulk_update(votes, ["a"])', 'bulk_update'),
                           ('Vote.get_or_create(a=1)', 'get_or_create'),
                           ('Vote.update_or_create(a=1)', 'update_or_create')):
            with self.subTest(call=call):
                tree = {**TREE, 'server/awards/editing.py': f'def edit(award, qs):\n    {call}\n'}
                out = run('server/awards/editing.py', tree=tree)
                self.assertIn(f'(calls .{attr}())', out)

    def test_a_call_with_a_positional_argument_is_not_an_orm_write(self):
        """openpyxl's `book.save(out)` and a dict's `.update({...})`."""
        tree = {**TREE, 'server/awards/editing.py':
                'def edit(book, out, params):\n    book.save(out)\n    params.update({})\n'
                '    params.delete(1)\n    params.create(2)\n'}
        self.assertIn('Postgres: not needed', run('server/awards/editing.py', tree=tree))

    def test_a_test_file_that_imports_models_needs_no_postgres(self):
        """Every test imports models; in a test only raw SQL counts."""
        tree = {**TREE, 'server/awards/tests/test_editing.py': 'from awards.models import Award\n'}
        self.assertIn('Postgres: not needed', run('server/awards/tests/test_editing.py', tree=tree))

    def test_raw_sql_is_database_code(self):
        tree = {**TREE, 'server/awards/editing.py': 'def f(c):\n    c.cursor()\n'}
        out = run('server/awards/editing.py', tree=tree)
        self.assertIn("as a whole module (raw SQL ('cursor('))", out)


class SharedUi(unittest.TestCase):
    def test_app_css_runs_the_whole_browser_suite(self):
        out = run('server/ui/static/ui/app.css')
        self.assertIn('[shared-ui]', out)
        self.assertIn('-m browser ui/tests/browser', commands(out))


class UiPage(unittest.TestCase):
    def test_kiosk_css_runs_the_kiosk_browser_files(self):
        out = run('server/ui/static/ui/kiosk.css')
        self.assertIn('[ui-page]', out)
        run_ = commands(out)
        self.assertIn('ui/tests/browser/test_kiosk_ondeck_labels.py', run_)
        self.assertIn('ui/tests/test_kiosk_pages.py', run_)
        self.assertNotIn('test_dashboard.py', run_)
        self.assertIn('DERBYNET_BROWSER_FONT="DejaVu Sans" uv run pytest -n auto -m '
                      'browser ui/tests/browser/test_kiosk_ondeck_labels.py', run_)

    def test_a_test_that_names_a_file_loaded_only_with_it_runs(self):
        """test_table_columns.py opens /ondeck/, which loads kiosk-ondeck.js
        beside kiosk.css."""
        out = run('server/ui/static/ui/kiosk.css')
        self.assertIn('ui/tests/browser/test_table_columns.py', commands(out))

    def test_a_name_is_matched_as_a_whole_word(self):
        """A test that names kiosk.json does not name kiosk.js."""
        tree = {**TREE, 'server/ui/static/ui/kiosk.js': '',
                'server/ui/tests/test_data_names.py': 'DATA = "kiosk.json"\n',
                'server/ui/tests/test_script_names.py': 'SCRIPT = "kiosk.js"\n'}
        out = run('server/ui/static/ui/kiosk.js', tree=tree)
        self.assertIn('ui/tests/test_script_names.py', out)
        self.assertNotIn('test_data_names.py', out)

    def test_a_name_php_also_has_is_not_matched_bare(self):
        tree = {**TREE, 'server/ui/static/ui/ondeck.css': '',
                'server/ui/templates/kiosk/pages/ondeck.html':
                    "{% static 'ui/ondeck.css' %}\n",
                'server/ui/tests/test_cites.py': 'CITE = "ondeck.css:49"\n'}
        out = run('server/ui/static/ui/ondeck.css', tree=tree)
        self.assertNotIn('test_cites.py', out)

    def test_a_template_runs_the_page_wide_checks(self):
        out = run('server/ui/templates/ui/dashboard/page.html')
        self.assertIn('ui/tests/test_navigation.py', commands(out))
        self.assertIn('ui/tests/test_query_growth.py', commands(out))

    def test_a_source_check_target_is_left_to_source_checks(self):
        out = run('server/ui/templates/ui/dashboard/page.html')
        self.assertIn('the commands leave them out: ui/tests/test_photo_urls.py', out)
        self.assertNotIn('test_photo_urls.py', commands(out))


class ServerTools(unittest.TestCase):
    def test_a_tool_runs_the_test_that_names_it(self):
        out = run('server/tools/pytest-under-load.sh')
        self.assertIn('[server-tools]', out)
        self.assertIn('tests/test_pytest_under_load.py', commands(out))


class Docker(unittest.TestCase):
    """No pytest builds the image, so a docker file prints the build and
    checks.sh: #58 pushed image.yml's steps unrun (#58 retro)."""

    def test_a_docker_file_builds_the_image_and_runs_its_checks(self):
        out = run('server/docker/entrypoint.sh')
        self.assertIn('[docker]', section(out, 'server/docker/entrypoint.sh'))
        self.assertNotIn('[fallback]', out)
        self.assertIn('docker/ci/checks.sh', docker_command(out))

    def test_each_paste_of_the_docker_command_has_its_own_prefix(self):
        """checks.sh removes everything under DERBYNET_TEST_PREFIX, so two
        runs of one printed prefix removed each other's containers (#58 retro
        review; CLAUDE.md gives each concurrent run its own prefix)."""
        line = docker_command(run('server/docker/entrypoint.sh'))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bin').mkdir()
            (root / 'server' / 'docker' / 'ci').mkdir(parents=True)
            log = root / 'log'
            stub(root / 'bin' / 'docker', f'echo "docker $*" >> {log}')
            stub(root / 'server' / 'docker' / 'ci' / 'checks.sh',
                 f'echo "checks $IMAGE $DERBYNET_TEST_PREFIX" >> {log}; exit 3')
            env = dict(os.environ, PATH=f'{root / "bin"}:{os.environ["PATH"]}')
            env.pop('DERBYNET_TEST_PREFIX', None)
            codes = [subprocess.run(['bash', '-c', line], cwd=root / 'server',
                                    env=env).returncode for _ in range(2)]
            calls = log.read_text().splitlines()
        self.assertEqual(codes, [3, 3], "the status is checks.sh's")
        checks = [c.split() for c in calls if c.startswith('checks ')]
        self.assertEqual(len(checks), 2, calls)
        (_, image1, prefix1), (_, image2, prefix2) = checks
        self.assertNotEqual(prefix1, prefix2)
        self.assertEqual(image1, f'derbynet_server:{prefix1}')
        self.assertIn(f'docker rmi derbynet_server:{prefix1}', calls)
        self.assertIn(f'docker rmi derbynet_server:{prefix2}', calls)

    def test_the_image_workflow_builds_the_image_too(self):
        out = run('.github/workflows/image.yml')
        self.assertIn('[docker]', section(out, '.github/workflows/image.yml'))
        self.assertIn('[everything]', section(out, '.github/workflows/image.yml'))
        self.assertIn('docker/ci/checks.sh', commands(out))

    def test_another_workflow_builds_no_image(self):
        self.assertNotIn('docker build', run('.github/workflows/server.yml'))


class PatchedPaths(unittest.TestCase):
    def test_a_mock_patch_path_counts_as_an_import(self):
        out = run('server/core/database_scripts.py')
        self.assertIn('legacy_api/tests/test_database_execute.py',
                      section(out, 'server/core/database_scripts.py').split('[importers]')[1])

    def test_a_monkeypatch_setattr_path_counts_as_an_import(self):
        out = run('server/legacy_api/handlers/race_control.py')
        self.assertIn('ui/tests/test_dashboard_poll.py',
                      section(out, 'server/legacy_api/handlers/race_control.py'))

    def test_a_monkeypatch_delattr_path_counts_as_an_import(self):
        out = run('server/core/database_scripts_gone.py')
        self.assertIn('core/tests/test_delattr.py',
                      section(out, 'server/core/database_scripts_gone.py').split('[importers]')[1])

    def test_a_dotted_string_in_another_call_does_not(self):
        out = run('server/racing/services/heats.py')
        self.assertNotIn('test_dashboard_poll.py', out)


class Command(unittest.TestCase):
    def test_a_command_runs_the_tests_that_name_it_and_the_guard(self):
        out = run('server/ui/management/commands/build_css.py')
        block = section(out, 'server/ui/management/commands/build_css.py')
        self.assertIn('[command]', block)
        self.assertIn('\n      ui/tests/test_build_css_command.py', block)
        self.assertIn('\n      tests/test_management_commands.py', block)
        self.assertNotIn('[fallback]', block)
        self.assertNotIn('[ui-python]', block)


class ToolPackage(unittest.TestCase):
    def test_a_packages_main_runs_the_tests_that_import_or_name_the_package(self):
        out = run('server/tools/standings_differential/__main__.py')
        block = section(out, 'server/tools/standings_differential/__main__.py')
        self.assertIn('racing/engine/tests/test_differential_controls.py', block)
        self.assertIn('racing/engine/tests/test_recorded.py', block)
        self.assertNotIn('[fallback]', block)

    def test_a_tool_no_test_names_says_so(self):
        out = run('server/tools/time_standings.py')
        self.assertIn('a server tool no test names or imports: no pytest target', out)
        self.assertNotIn('[fallback]', out)


class Fallback(unittest.TestCase):
    def test_an_unmapped_server_file_falls_back_to_its_app_and_says_so(self):
        out = run('server/ui/middleware.py')
        self.assertIn('[fallback] no rule maps it to a test', out)
        self.assertIn('uv run pytest -n auto ui/tests', commands(out))

    def test_a_top_level_server_file_falls_back_to_server_tests(self):
        out = run('server/manage.py')
        self.assertIn('[fallback]', out)
        self.assertIn('uv run pytest -n auto tests', commands(out))

    def test_an_unmapped_file_outside_server_leaves_everything_to_ci(self):
        out = run('.gitattributes')
        self.assertIn('[fallback] no rule maps it and it is outside server/: '
                      "CI's full run covers it", out)
        self.assertIn('CI runs the full suites on both databases (D85)', out)
        self.assertIn("(no pytest target: CI's full run covers the diff, D85)",
                      commands(out))


class BeforeTheHandOff(unittest.TestCase):
    """#312 restarted its hand-off run for a docstring number no assert read,
    which docstring_claims.py lists, and never ran operand_mutants.py."""

    def test_a_changed_test_file_lists_its_docstring_claims(self):
        out = run('server/ui/tests/test_dashboard.py', 'server/ui/tests/test_kiosk_pages.py')
        self.assertIn('  uv run python tools/docstring_claims.py ui/tests/test_dashboard.py '
                      'ui/tests/test_kiosk_pages.py\n', before_hand_off(out))
        self.assertNotIn('docstring_claims', commands(out))
        self.assertNotIn('operand_mutants', out)

    def test_the_body_is_checked_against_every_changed_test_file(self):
        """#341 ran `--check` on its two files with new tests and covered two
        other changed files with one catch-all row; review's `--check` on
        every changed file found 28 claims unlisted, and the hand-off
        restarted. #317 added 11 rows after a merge (#317 and #341 retro)."""
        out = run('server/ui/tests/test_dashboard.py', 'server/ui/tests/test_kiosk_pages.py')
        self.assertIn('  uv run python tools/docstring_claims.py --check <the PR body draft> '
                      'ui/tests/test_dashboard.py ui/tests/test_kiosk_pages.py\n',
                      before_hand_off(out))

    def test_a_changed_test_file_lists_its_helpers_mutants(self):
        """--listed skips a test file unless --file names it: #345's
        scanner lived in tests/test_docker_checks.py (#345 retro, PR #364's
        review)."""
        out = run('server/ui/tests/test_dashboard.py', 'server/ui/tests/test_kiosk_pages.py')
        for path in ('ui/tests/test_dashboard.py', 'ui/tests/test_kiosk_pages.py'):
            self.assertIn(f'  uv run python tools/mutate.py --listed --file {path} -- '
                          '<the node ids of the tests that call its helpers>\n',
                          before_hand_off(out))
        self.assertNotIn('mutate.py', commands(out))

    def test_a_changed_source_file_lists_its_operand_mutants(self):
        for path in ('server/awards/editing.py', 'server/ui/static/ui/kiosk.css',
                     'server/ui/static/ui/kiosk-ondeck.js'):
            with self.subTest(path=path):
                out = run(path)
                self.assertIn('  uv run python tools/operand_mutants.py\n',
                              before_hand_off(out))
                self.assertIn('  uv run python tools/mutate.py --listed -- <the node ids',
                              before_hand_off(out))
                self.assertNotIn('operand_mutants', commands(out))
                self.assertNotIn('mutate.py', commands(out))
                self.assertNotIn('docstring_claims', out)

    def test_a_test_helper_a_deleted_file_or_a_doc_lists_neither(self):
        out = run('server/core/tests/factories.py', 'server/awards/gone.py',
                  'server/docs/x.md', 'server/tools/source-checks.sh')
        self.assertNotIn('operand_mutants', out)
        self.assertNotIn('docstring_claims', out)
        self.assertNotIn('Before the first hand-off run', out)

    def test_a_plan_tool_and_its_test_list_both(self):
        """#320's diff was `plan/bin/affected-tests` and its test, and the
        tool printed neither line, so its implementer listed claims and
        mutants by hand (#320 retro). Paths are from server/."""
        tree = {**TREE, 'plan/bin/tool': '#!/usr/bin/env python3\nif a:\n    pass\n',
                'plan/tests/test_tool.py': '"""2 runs."""\n',
                'plan/bin/hook': '#! /bin/bash\n', 'plan/bin/tool.sh': '#!python\n',
                'plan/bin/notes': 'python, with no shebang\n'}
        out = run('plan/bin/tool', 'plan/tests/test_tool.py', tree=tree)
        self.assertIn('  uv run python tools/docstring_claims.py ../plan/tests/test_tool.py\n',
                      before_hand_off(out))
        self.assertIn('  uv run python tools/operand_mutants.py\n', before_hand_off(out))
        out = run('plan/bin/hook', 'plan/bin/tool.sh', 'plan/bin/notes', 'plan/bin/gone',
                  'plan/README.md',
                  'plan/tests/gone.py', tree=tree)
        self.assertNotIn('Before the first hand-off run', out)

    def test_a_migration_or_vendored_code_lists_no_mutants(self):
        tree = {**TREE, 'server/racing/migrations/0002_x.py': 'X = 1\n',
                'server/ui/static/ui/vendor/lib-1.0.js': 'if (a && b) {}\n'}
        out = run('server/racing/migrations/0002_x.py',
                  'server/ui/static/ui/vendor/lib-1.0.js', tree=tree)
        self.assertNotIn('operand_mutants', out)


class GitDiff(unittest.TestCase):
    def test_it_reads_database_code_from_the_diff_only(self):
        """In a module that imports no models module and makes no ORM write,
        only the changed lines count: an edit beside a queryset needs no
        Postgres; adding a queryset does."""
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                   GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_NOSYSTEM='1')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*argv):
                subprocess.run(['git', *argv], cwd=root, env=env, check=True,
                               capture_output=True)

            for rel, text in TREE.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            editing = root / 'server/awards/editing.py'
            editing.write_text('Award.objects.all()\n\n\nNAME = "a"\n')
            git('init', '-q', '-b', 'master')
            git('add', '.')
            git('commit', '-q', '-m', 'base')
            editing.write_text('Award.objects.all()\n\n\nNAME = "b"\n')
            git('commit', '-q', '-am', 'rename')

            def affected():
                return subprocess.run(
                    [sys.executable, '-I', str(AFFECTED), 'HEAD~1'], cwd=root, env=env,
                    capture_output=True, text=True, check=True).stdout

            out = affected()
            self.assertIn('server/awards/editing.py\n', out)
            self.assertIn('Postgres: not needed', out)
            self.assertIn('  uv run python tools/operand_mutants.py --base HEAD~1\n',
                          before_hand_off(out))
            self.assertIn('  uv run python tools/mutate.py --listed --base HEAD~1 -- ',
                          before_hand_off(out))
            editing.write_text('Award.objects.all()\n\n\nNAME = Award.objects.get()\n')
            out = affected()
            self.assertIn('Postgres: needed', out)

    def edit_the_real_module(self, rel):
        """The tool's output for a comment added to the real `rel`, an edit
        that touches no ORM line."""
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                   GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_NOSYSTEM='1')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*argv):
                subprocess.run(['git', *argv], cwd=root, env=env, check=True,
                               capture_output=True)

            for path, text in {**TREE, rel: (REPO / rel).read_text()}.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(text)
            git('init', '-q', '-b', 'master')
            git('add', '.')
            git('commit', '-q', '-m', 'base')
            with open(root / rel, 'a') as f:
                f.write('# A comment.\n')
            git('commit', '-q', '-am', 'comment')
            return subprocess.run(
                [sys.executable, '-I', str(AFFECTED), 'HEAD~1'], cwd=root, env=env,
                capture_output=True, text=True, check=True).stdout

    def test_a_non_orm_edit_to_awards_editing_needs_postgres(self):
        """#288's known limit: a module that writes the database counts
        whatever lines changed."""
        out = self.edit_the_real_module('server/awards/editing.py')
        self.assertIn('server/awards/editing.py: database code, as a whole module', out)

    def test_a_non_orm_edit_to_transfer_importer_needs_postgres(self):
        out = self.edit_the_real_module('server/transfer/importer.py')
        self.assertIn('server/transfer/importer.py: database code, as a whole module', out)



# D97's stand-in: a browser test file that moves BOUND, bound and ready into
# helpers.py, as #313 moved them out of test_awards.py and test_kiosks.py.
SHEET = 'server/ui/tests/browser/test_sheet.py'
HELPERS = 'server/ui/tests/browser/helpers.py'
CONFTEST = 'server/ui/tests/browser/conftest.py'
MOVED = '''
# True once htmx has bound it.
BOUND = "root => true"


def bound(page, locator):
    """Wait until htmx has bound `locator`."""
    page.wait_for_function(BOUND, arg=locator)


def ready(page, body):
    expect(body).to_be_visible()
    bound(page, body)
    return body
'''
TEST = '''

def test_sheet(page):
    expect(ready(page, page.locator("body"))).to_be_visible()
    page.wait_for_timeout(10)
    shoot(page, "sheet")
'''
BASE_SHEET = ('"""The sheet."""\n\nfrom playwright.sync_api import expect\n\n'
              'from ui.tests.browser.conftest import shoot\n' + MOVED + TEST)
MOVED_SHEET = ('"""The sheet."""\n\nfrom playwright.sync_api import expect\n\n'
               'from ui.tests.browser.conftest import shoot\n'
               'from ui.tests.browser.helpers import bound, ready\n' + TEST)
BASE_HELPERS = '"""Shared helpers."""\n\nfrom playwright.sync_api import expect\n'
MOVED_HELPERS = BASE_HELPERS + MOVED
LOAD_RUN = 'pytest-under-load.sh -m browser -n 4 --count 25 ui/tests/browser/test_sheet.py'
AUTOUSE = ('\n\n@pytest.fixture(autouse=True)\ndef slow_network(page):\n'
           '    page.route("**", lambda r: r.continue_())\n')


def load_tool():
    """plan/bin/affected-tests as a module, so that a near miss calls
    import_only() without a git repository and a run of the tool (#320
    review: the pre-push hook runs these)."""
    loader = importlib.machinery.SourceFileLoader('affected_tests_tool', str(AFFECTED))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


TOOL = load_tool()


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
               GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM='1')
    return env


def moved(sheet=MOVED_SHEET, helpers=MOVED_HELPERS, base=BASE_SHEET, commit=True,
          extra=None):
    """The tool's output for a branch that turns test_sheet.py from `base`
    (None: no such file at BASE) into `sheet`, and helpers.py into `helpers`,
    with the files of `extra` as they are at BASE and HEAD; `commit=False`
    leaves the edits uncommitted."""
    env = git_env()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def git(*argv):
            subprocess.run(['git', *argv], cwd=root, env=env, check=True,
                           capture_output=True)

        def write(files):
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)

        write({**TREE, HELPERS: BASE_HELPERS, **(extra or {})})
        if base is not None:
            write({SHEET: base})
        git('init', '-q', '-b', 'master')
        git('add', '.')
        git('commit', '-q', '-m', 'base')
        write({SHEET: sheet, HELPERS: helpers})
        if commit:
            git('add', '.')
            git('commit', '-q', '-m', 'move')
        return subprocess.run(
            [sys.executable, '-I', str(AFFECTED), 'HEAD~1' if commit else 'HEAD'],
            cwd=root, env=env, capture_output=True, text=True, check=True).stdout


def verdict(sheet=MOVED_SHEET, helpers=MOVED_HELPERS, base=BASE_SHEET, extra=None):
    """import_only() on test_sheet.py from `base` to `sheet`, with helpers.py
    as `helpers` and the stand-in's other modules, plus `extra`, at HEAD."""
    files = {**TREE, HELPERS: helpers, **(extra or {})}

    def module_text(name):
        rel = 'server/' + name.replace('.', '/')
        return files.get(f'{rel}.py', files.get(f'{rel}/__init__.py'))

    return TOOL.import_only(base, sheet, module_text)


def repeats(out):
    """The "Repeats under load" lines, each ending in a newline."""
    return out.split('Repeats under load', 1)[1].split('\n\n', 1)[0] + '\n'


class ImportOnlyThroughGit(unittest.TestCase):
    """D97 end to end: a git branch, the tool's output. The near misses are
    in ImportOnly, in process (#320)."""

    def assert_keeps(self, out, why, targets=''):
        """The count, D97's reason, and the command: the whole file, or the
        node ids `targets` adds to it."""
        self.assertIn('ui/tests/browser/test_sheet.py: 25 repeats, involves timing',
                      repeats(out))
        self.assertIn(f'not an import-only move (D97): {why}', repeats(out))
        self.assertIn(f'      tools/{LOAD_RUN}{targets}\n', repeats(out))

    def test_a_move_takes_no_repeats(self):
        out = moved()
        self.assertIn('  ui/tests/browser/test_sheet.py: import-only (D97): none\n'
                      '      moves BOUND, bound, ready unchanged to '
                      'ui.tests.browser.helpers, which it imports from; a flake ticket '
                      'still repeats it 25 times', repeats(out))
        self.assertNotIn('pytest-under-load.sh', repeats(out))
        # Only its load run goes: it still runs, and its claims are listed.
        self.assertIn('-m browser ui/tests/browser', commands(out))
        self.assertIn('ui/tests/browser/test_sheet.py', before_hand_off(out))
        self.assertNotIn('--stdin has no BASE', out)

    def test_an_uncommitted_move_takes_no_repeats(self):
        self.assertIn('test_sheet.py: import-only (D97): none', moved(commit=False))

    def test_a_copy_that_differs_by_one_line_keeps_them(self):
        helpers = MOVED_HELPERS.replace('    return body\n', '    return body.first\n')
        self.assert_keeps(moved(helpers=helpers), 'ui.tests.browser.helpers has '
                          'ready otherwise than the file had it')

    def test_a_deletion_with_no_import_keeps_them(self):
        """A helper nothing reads, deleted with no import."""
        spare = '\n\ndef spare(page):\n    return page\n'
        self.assert_keeps(moved(base=BASE_SHEET + spare, sheet=BASE_SHEET),
                          'it deletes spare, which it neither imports by name nor '
                          'moves with code it imports')

    def test_a_test_added_beside_the_move_repeats_it_alone(self):
        """D97 keeps the count, and D101 finds the rest of the diff a move."""
        sheet = MOVED_SHEET + '\n\ndef test_more(page):\n    shoot(page, "more")\n'
        out = moved(sheet=sheet)
        self.assert_keeps(out, 'line 15 is new or changed', '::test_more')
        self.assertIn('        test_more: new\n      a flake ticket repeats the whole '
                      'file ui/tests/browser/test_sheet.py 25 times instead, on the '
                      'database it failed on (D101)\n', repeats(out))

    def test_a_file_new_on_the_branch_keeps_them(self):
        self.assert_keeps(moved(base=None), 'new on the branch')

    def test_a_name_imported_from_outside_the_tree_keeps_them(self):
        """Through the tool's own module_text(), which finds no file."""
        sheet = MOVED_SHEET.replace('ui.tests.browser.helpers', 'browser_helpers')
        self.assert_keeps(moved(sheet=sheet),
                          'bound comes from browser_helpers, which is not in the tree')

    def test_stdin_keeps_them(self):
        """--stdin has no BASE to read the file at."""
        tree = {**TREE, SHEET: MOVED_SHEET, HELPERS: MOVED_HELPERS}
        out = run(SHEET, HELPERS, tree=tree)
        self.assertIn('--stdin has no BASE to compare with, so no file counts as an '
                      'import-only move, D97: each keeps its count', repeats(out))
        self.assertIn(f'      tools/{LOAD_RUN}\n', repeats(out))
        self.assertIn('the whole file (D101): --stdin has no BASE to compare with',
                      repeats(out))
        self.assertNotIn('import-only (D97): none', out)


class ImportOnly(unittest.TestCase):
    """D97: a browser test file whose diff only moves top-level code,
    unchanged, into a module it imports takes no repeats under load. Each
    case that keeps them changes the import-only move in one way, so the
    check it names is what keeps them (#320)."""

    def assert_keeps(self, why, **files):
        with self.assertRaises(TOOL.NotImportOnly) as caught:
            verdict(**files)
        self.assertIn(why, str(caught.exception))

    def test_the_move_takes_bound_and_ready_and_their_bound(self):
        """BOUND isn't imported: only the moved bound() read it."""
        self.assertEqual(verdict(), {'ui.tests.browser.helpers': ['BOUND', 'bound', 'ready']})

    def test_a_copy_with_a_new_docstring_keeps_them(self):
        """#313's open_section: the copy gained a docstring."""
        helpers = MOVED_HELPERS.replace('def ready(page, body):\n',
                                        'def ready(page, body):\n    """Ready."""\n')
        self.assert_keeps('ui.tests.browser.helpers has ready otherwise than the file '
                          'had it', helpers=helpers)

    def test_a_deleted_name_still_used_but_not_imported_keeps_them(self):
        sheet = MOVED_SHEET.replace('import bound, ready', 'import bound')
        self.assert_keeps('ready is deleted and still used, but not imported', sheet=sheet)

    def test_a_dependency_the_file_still_reads_keeps_them(self):
        """BOUND moves with bound(), but a test the file keeps reads it."""
        reads = ('    shoot(page, "sheet")\n', '    shoot(page, BOUND)\n')
        self.assert_keeps('BOUND is deleted and still used, but not imported',
                          base=BASE_SHEET.replace(*reads),
                          sheet=MOVED_SHEET.replace(*reads))

    def test_a_decorated_dependency_keeps_them(self):
        """ready reads a cached helper the file doesn't import."""
        cached = '\n\n@functools.cache\ndef locate(body):\n    return body\n'
        reads = ('    return body\n', '    return locate(body)\n')
        base = 'import functools\n' + BASE_SHEET.replace(*reads).replace(
            '\n\ndef test_sheet', cached + '\n\ndef test_sheet')
        helpers = 'import functools\n' + MOVED_HELPERS.replace(*reads) + cached
        self.assert_keeps('locate moves with code that reads it, but is decorated: '
                          'import it by name', base=base,
                          sheet='import functools\n' + MOVED_SHEET, helpers=helpers)

    def test_a_dependency_built_by_a_call_keeps_them(self):
        call = ('BOUND = "root => true"', 'BOUND = str("root => true")')
        self.assert_keeps('BOUND moves with code that reads it, but is built by a '
                          'call: import it by name', base=BASE_SHEET.replace(*call),
                          helpers=MOVED_HELPERS.replace(*call))

    def test_a_fixture_deleted_by_presence_keeps_them(self):
        """pytest reads a fixture from the file by its presence: an autouse
        one, or one that overrides a conftest's, is no dependency of the
        moved code (#320 review, H1)."""
        override = '\n\n@pytest.fixture\ndef viewport_sizes():\n    return ["phone"]\n'
        for name, fixture in (('slow_network', AUTOUSE), ('viewport_sizes', override)):
            with self.subTest(name=name):
                self.assert_keeps(
                    f'it deletes {name}, which it neither imports by name nor moves '
                    'with code it imports',
                    base='import pytest\n' + BASE_SHEET + fixture,
                    sheet='import pytest\n' + MOVED_SHEET,
                    helpers='import pytest\n' + MOVED_HELPERS + fixture)

    def test_a_fixture_deleted_into_a_conftest_keeps_them(self):
        """An identical autouse fixture in a conftest the file imports from,
        in its own folder or another, is no move."""
        for conftest in (CONFTEST, 'server/ui/tests/other/conftest.py'):
            with self.subTest(conftest=conftest):
                module = conftest[len('server/'):-len('.py')].replace('/', '.')
                imports = ('from ui.tests.browser.conftest import shoot\n',
                           f'from {module} import shoot\n')
                self.assert_keeps(
                    'it deletes slow_network, which it neither imports by name nor '
                    'moves with code it imports',
                    base='import pytest\n' + BASE_SHEET.replace(*imports) + AUTOUSE,
                    sheet='import pytest\n' + MOVED_SHEET.replace(*imports),
                    extra={conftest: 'import pytest\n\n\ndef shoot(page, name):\n'
                                     '    pass\n' + AUTOUSE})

    def test_moved_code_that_reads_a_global_the_file_keeps_keeps_them(self):
        """The file keeps SEEN, which the moved note() appends to: the file
        and helpers.py would each have a list (#320 review, H2)."""
        seen = 'SEEN = []\n\n\ndef note(x):\n    SEEN.append(x)\n\n\n# True once'
        check = ('    shoot(page, "sheet")', '    note(1)\n    assert SEEN == [1]\n'
                 '    shoot(page, "sheet")')
        self.assert_keeps(
            'note reads SEEN, which the file keeps, so the file and '
            'ui.tests.browser.helpers would each have one',
            base=BASE_SHEET.replace('# True once', seen).replace(*check),
            sheet=MOVED_SHEET.replace('import bound, ready', 'import bound, note, ready')
            .replace('\n\ndef test_sheet', '\nSEEN = []\n\n\ndef test_sheet', 1)
            .replace(*check),
            helpers=MOVED_HELPERS.replace('# True once', seen))

    def test_an_import_dropped_for_its_effect_keeps_them(self):
        """An autouse fixture imported for pytest to find, and a module
        imported for what it does when imported, read by no code (#320
        review, H3)."""
        fixture = 'from ui.tests.browser.helpers import slow_network  # noqa: F401\n'
        plugin = 'import ui.tests.browser.plugin_clock  # noqa: F401\n'
        clock = {'server/ui/tests/browser/plugin_clock.py': 'import time\n'}
        for name, base, sheet, helpers, why in (
                ('autouse', BASE_SHEET.replace('import shoot\n', 'import shoot\n' + fixture),
                 MOVED_SHEET, 'import pytest\n' + MOVED_HELPERS + AUTOUSE,
                 'it drops the import of slow_network, which no moved code read'),
                ('plugin', plugin + BASE_SHEET, MOVED_SHEET, MOVED_HELPERS,
                 'it drops the import of ui, which no moved code read'),
                ('plugin beside another', plugin + 'import ui.tests.browser.conftest\n'
                 + BASE_SHEET, 'import ui.tests.browser.conftest\n' + MOVED_SHEET,
                 MOVED_HELPERS, 'it imports ui otherwise than BASE did')):
            with self.subTest(name=name):
                self.assert_keeps(why, base=base, sheet=sheet, helpers=helpers,
                                  extra=clock)

    def test_an_import_nothing_read_dropped_keeps_them(self):
        """`import re`, read by nothing, may be there for its effect."""
        self.assert_keeps('it drops the import of re, which no moved code read',
                          base='import re\n' + BASE_SHEET)

    def test_an_import_only_the_moved_code_read_dropped_takes_no_repeats(self):
        """As ruff asks after a move: `re` left with ready, the code that read it."""
        reads = ('    return body\n', '    return body or re\n')
        self.assertEqual(verdict(base='import re\n' + BASE_SHEET.replace(*reads),
                                 helpers='import re\n' + MOVED_HELPERS.replace(*reads)),
                         {'ui.tests.browser.helpers': ['BOUND', 'bound', 'ready']})

    def test_an_assignment_expression_outside_a_function_keeps_them(self):
        """A walrus binds a module name bindings() doesn't see: in a kept
        statement, before the moved def, in a default, or in helpers.py
        (#320 review, H4)."""
        for name, files, why in (
                ('kept statement', dict(
                    base=BASE_SHEET.replace('\n\ndef test_sheet',
                                            '\n\nPROBE = (bound := print)\n\n\ndef test_sheet'),
                    sheet=MOVED_SHEET.replace('\n\ndef test_sheet',
                                              '\n\nPROBE = (bound := print)\n\n\ndef test_sheet')),
                 'BASE line 22: an assignment expression (:=) outside a function'),
                ('before the def', dict(
                    base=BASE_SHEET.replace('# True once',
                                            'PROBE = (bound := print)\n\n\n# True once'),
                    sheet=MOVED_SHEET.replace('\n\ndef test_sheet',
                                              '\nPROBE = (bound := print)\n\n\ndef test_sheet', 1)),
                 'line 7: an assignment expression (:=) outside a function'),
                ('default', dict(
                    base=BASE_SHEET.replace('# True once', 'def early(x=(ready := None)):\n'
                                            '    return x\n\n\n# True once'),
                    sheet=MOVED_SHEET.replace('\n\ndef test_sheet', '\ndef early(x=(ready := '
                                              'None)):\n    return x\n\n\ndef test_sheet', 1)),
                 'line 7: an assignment expression (:=) outside a function'),
                ('helpers.py', dict(helpers=MOVED_HELPERS + '\n\nPROBE = (BOUND := "")\n'),
                 'ui.tests.browser.helpers line 20: an assignment expression')):
            with self.subTest(name=name):
                self.assert_keeps(why, **files)

    def test_an_assignment_expression_in_a_function_body_does_not(self):
        """In ready's body, or a kept lambda's: it binds a local."""
        reads = ('    return body\n', '    if (shown := body):\n        return shown\n')
        self.assertEqual(verdict(base=BASE_SHEET.replace(*reads),
                                 helpers=MOVED_HELPERS.replace(*reads)),
                         {'ui.tests.browser.helpers': ['BOUND', 'bound', 'ready']})
        key = '\n\nKEY = lambda b: (s := b) and s\n'
        self.assertEqual(verdict(base=BASE_SHEET + key, sheet=MOVED_SHEET + key),
                         {'ui.tests.browser.helpers': ['BOUND', 'bound', 'ready']})

    def test_future_imports_that_differ_keep_them(self):
        """`from __future__ import annotations` makes the file's annotations
        strings, and helpers.py's not."""
        future = 'from __future__ import annotations\n'
        self.assert_keeps('the file and ui.tests.browser.helpers differ in their '
                          '`from __future__` imports',
                          base=future + BASE_SHEET, sheet=future + MOVED_SHEET)

    def test_a_kept_statement_that_reads_a_moved_name_does_not(self):
        """A top-level call reads `expect`, which ready reads: a read is no
        binding (the ctx operand in stored_names)."""
        call = '\n\nexpect.set_options(timeout=5_000)\n'
        self.assertEqual(verdict(base=BASE_SHEET + call, sheet=MOVED_SHEET + call),
                         {'ui.tests.browser.helpers': ['BOUND', 'bound', 'ready']})

    def test_a_changed_body_keeps_them(self):
        sheet = MOVED_SHEET.replace('wait_for_timeout(10)', 'wait_for_timeout(20)')
        self.assert_keeps('line 9 is new or changed', sheet=sheet)

    def test_an_edited_docstring_keeps_them(self):
        sheet = MOVED_SHEET.replace('"""The sheet."""', '"""The sheet, moved."""')
        self.assert_keeps('line 1 is new or changed', sheet=sheet)

    def test_a_new_comment_keeps_them(self):
        sheet = MOVED_SHEET.replace('def test_sheet', '# Moved.\ndef test_sheet')
        self.assert_keeps('a comment is new or changed: # Moved.', sheet=sheet)

    def test_a_comment_on_an_import_line_does_not(self):
        sheet = MOVED_SHEET.replace('import bound, ready', 'import bound, ready  # noqa')
        self.assertIn('ui.tests.browser.helpers', verdict(sheet=sheet))

    def test_a_kept_line_reformatted_takes_no_repeats(self):
        """Compared by AST: ruff's quotes or line breaks change no code."""
        sheet = MOVED_SHEET.replace('shoot(page, "sheet")', "shoot(\n        page, 'sheet'\n    )")
        self.assertIn('ui.tests.browser.helpers', verdict(sheet=sheet))

    def test_a_syntax_error_keeps_them(self):
        for files, why in (
                ({'sheet': MOVED_SHEET + 'def (:\n'}, 'cannot be parsed'),
                ({'helpers': MOVED_HELPERS + 'def (:\n'},
                 'ui.tests.browser.helpers cannot be parsed')):
            with self.subTest(why=why):
                self.assert_keeps(why, **files)

    def test_a_relative_import_keeps_them(self):
        """Kept on both sides of a move that is otherwise import-only."""
        add = 'from .conftest import shoot as snap\n'
        self.assert_keeps('BASE line 1: a relative import', base=add + BASE_SHEET,
                          sheet=add + MOVED_SHEET)

    def test_a_star_import_keeps_them(self):
        add = 'from ui.tests.browser.conftest import *\n'
        self.assert_keeps('BASE line 1: a star import', base=add + BASE_SHEET,
                          sheet=add + MOVED_SHEET)

    def test_a_module_import_keeps_them(self):
        """`import x`, with the moved name read as `x.f` or as before."""
        add = MOVED_SHEET.replace('from ui.tests.browser.helpers import bound, ready',
                                  'import ui.tests.browser.helpers')
        for sheet, why in (
                (add.replace('expect(ready(', 'expect(ui.tests.browser.helpers.ready('),
                 'line 9 is new or changed'),
                (add, 'ready is deleted and still used, but not imported')):
            with self.subTest(why=why):
                self.assert_keeps(why, sheet=sheet)

    def test_a_module_imported_whole_is_not_imported_from(self):
        """`import M as m` is no `from M import`, so a deleted helper nothing
        reads, which M has, is no move."""
        spare = '\n\ndef spare(page):\n    return page\n'
        self.assert_keeps('it deletes spare, which it neither imports by name nor '
                          'moves with code it imports',
                          base=BASE_SHEET + spare,
                          sheet='import ui.tests.browser.helpers as helpers\n' + BASE_SHEET,
                          helpers=MOVED_HELPERS + spare)

    def test_a_name_bound_twice_keeps_them(self):
        sheet = MOVED_SHEET.replace('import bound, ready',
                                    'import bound, ready, ready as ready')
        self.assert_keeps('the file binds ready more than once', sheet=sheet)

    def test_a_moved_name_imported_under_another_name_keeps_them(self):
        """helpers.py has ready as it was, but the file binds ready to spare."""
        spare = '\n\ndef spare(page, body):\n    return body\n'
        sheet = MOVED_SHEET.replace('import bound, ready', 'import bound, spare as ready')
        self.assert_keeps('ready is imported, but not as `from M import ready`',
                          sheet=sheet, helpers=MOVED_HELPERS + spare)

    def test_a_moved_name_imported_as_a_module_keeps_them(self):
        sheet = MOVED_SHEET.replace('import bound, ready', 'import bound\nimport ready')
        self.assert_keeps('ready is imported, but not as `from M import ready`',
                          sheet=sheet)

    def test_deleting_one_of_two_defs_of_a_name_keeps_them(self):
        """The file still defines ready, so nothing moved it."""
        stub = '\n\ndef ready(page, body):\n    return body\n'
        base = BASE_SHEET.replace('\n\ndef test_sheet', stub + '\n\ndef test_sheet')
        sheet = MOVED_SHEET.replace('import bound, ready', 'import bound').replace(
            '\n\ndef test_sheet', stub + '\n\ndef test_sheet')
        self.assert_keeps('the file still defines ready', base=base, sheet=sheet)

    def test_moved_code_that_reads_a_name_bound_otherwise_keeps_them(self):
        """ready's `expect` comes from another module in helpers.py."""
        helpers = MOVED_HELPERS.replace('from playwright.sync_api import expect',
                                        'from ui.tests.browser.conftest import expect')
        self.assert_keeps('ready reads expect, which ui.tests.browser.helpers binds '
                          'otherwise than the file did', helpers=helpers)

    def test_moved_code_that_reads_a_module_dunder_keeps_them(self):
        """`__file__` names another file in helpers.py."""
        base = BASE_SHEET.replace('BOUND = "root => true"', 'BOUND = __file__')
        helpers = MOVED_HELPERS.replace('BOUND = "root => true"', 'BOUND = __file__')
        self.assert_keeps('BOUND reads __file__, which ui.tests.browser.helpers binds '
                          'otherwise than the file did', base=base, helpers=helpers)

    def test_moved_code_that_reads_a_name_the_module_lacks_keeps_them(self):
        """helpers.py imports no `expect`, which ready reads."""
        helpers = MOVED_HELPERS.replace('from playwright.sync_api import expect\n', '')
        self.assert_keeps('ready reads expect, which ui.tests.browser.helpers binds '
                          'otherwise than the file did', helpers=helpers)

    def test_moved_code_that_reads_a_builtin_the_module_shadows_keeps_them(self):
        reads = ('    return body\n', '    return len(body)\n')
        helpers = MOVED_HELPERS.replace(*reads) + '\n\ndef len(x):\n    return 0\n'
        self.assert_keeps('ready reads len, which ui.tests.browser.helpers binds '
                          'otherwise than the file did',
                          base=BASE_SHEET.replace(*reads), helpers=helpers)

    def test_moved_code_that_reads_a_name_the_module_defines_itself_keeps_them(self):
        """ready's `expect`, imported in the file, is a def in helpers.py."""
        helpers = MOVED_HELPERS.replace('from playwright.sync_api import expect\n',
                                        'def expect(x):\n    return x\n')
        self.assert_keeps('ready reads expect, which ui.tests.browser.helpers binds '
                          'otherwise than the file did', helpers=helpers)

    def test_moved_code_that_reads_a_constant_the_module_imports_keeps_them(self):
        helpers = MOVED_HELPERS.replace('BOUND = "root => true"',
                                        'from ui.tests.browser.conftest import BOUND')
        self.assert_keeps('bound reads BOUND, which ui.tests.browser.helpers binds '
                          'otherwise than the file did', helpers=helpers)

    def test_moved_code_that_reads_a_module_alias_bound_otherwise_keeps_them(self):
        reads = ('    return body\n', '    return body or c.X\n')
        self.assert_keeps(
            'ready reads c, which ui.tests.browser.helpers binds otherwise than the '
            'file did',
            base='import ui.tests.browser.conftest as c\n' + BASE_SHEET.replace(*reads),
            sheet='import ui.tests.browser.conftest as c\n' + MOVED_SHEET,
            helpers='import json as c\n' + MOVED_HELPERS.replace(*reads))

    def test_moved_code_that_reads_a_name_bound_in_a_block_keeps_them(self):
        """bound reads LIMIT, which the file binds inside a statement that is
        no plain def, assignment or import, and helpers.py lacks."""
        for binder in ('if True:\n    LIMIT = 1\n',
                       'if True:\n    def LIMIT():\n        pass\n',
                       'try:\n    import json as LIMIT\nexcept ImportError:\n    pass\n',
                       'try:\n    pass\nexcept Exception as LIMIT:\n    pass\n',
                       'match 1:\n    case LIMIT:\n        pass\n',
                       'match []:\n    case [*LIMIT]:\n        pass\n',
                       'match {}:\n    case {**LIMIT}:\n        pass\n'):
            with self.subTest(binder=binder):
                reads = ('page.wait_for_function(BOUND, arg=locator)',
                         'page.wait_for_function(BOUND, arg=locator, timeout=LIMIT)')
                self.assert_keeps(
                    'BASE binds LIMIT more than once, or not by a plain def, '
                    'assignment or import',
                    base=BASE_SHEET.replace(*reads).replace(
                        '\n# True once', f'\n{binder}\n\n# True once'),
                    sheet=MOVED_SHEET.replace('\n\ndef test_sheet',
                                              f'\n{binder}\n\ndef test_sheet'),
                    helpers=MOVED_HELPERS.replace(*reads))

    def test_a_name_rebound_through_global_keeps_them(self):
        """reset() rebinds the file's BOUND, which no longer moves helpers.py's."""
        reset = '\n\ndef reset():\n    global BOUND\n    BOUND = ""\n'
        self.assert_keeps('the file binds BOUND more than once',
                          base=BASE_SHEET + reset, sheet=MOVED_SHEET + reset)

    def test_a_name_bound_by_a_def_and_an_import_keeps_them(self):
        """helpers.py binds ready's `expect` twice, in either order."""
        twice = '\n\ndef expect(x):\n    return x\n'
        for helpers in (MOVED_HELPERS + twice,
                        MOVED_HELPERS.replace('from playwright.sync_api import expect\n',
                                              twice + '\n\nfrom playwright.sync_api '
                                              'import expect\n')):
            with self.subTest(helpers=helpers[:60]):
                self.assert_keeps('ui.tests.browser.helpers binds expect more than once',
                                  helpers=helpers)

    def test_a_module_and_its_submodule_imported_take_no_repeats(self):
        """`import os` and `import os.path` both bind os, alike on both sides."""
        imports = 'import os\nimport os.path\n'
        reads = ('    return body\n', '    return body or os.sep\n')
        self.assertIn('ui.tests.browser.helpers', verdict(
            base=imports + BASE_SHEET.replace(*reads), sheet=imports + MOVED_SHEET,
            helpers=imports + MOVED_HELPERS.replace(*reads)))

    def test_an_import_that_rebinds_a_name_keeps_them(self):
        sheet = MOVED_SHEET.replace('from ui.tests.browser.conftest import shoot',
                                    'from ui.tests.browser.helpers import shoot')
        self.assert_keeps('it imports shoot otherwise than BASE did', sheet=sheet)

    def test_an_import_of_a_new_name_keeps_them(self):
        sheet = MOVED_SHEET.replace('import shoot', 'import shoot, zoom')
        self.assert_keeps('it imports zoom, which is no moved definition', sheet=sheet)

    def test_an_import_dropped_while_still_used_keeps_them(self):
        sheet = MOVED_SHEET.replace('from playwright.sync_api import expect\n', '')
        self.assert_keeps('expect is no longer imported but still used', sheet=sheet)

    def test_moving_what_pytest_reads_keeps_them(self):
        """A test, a hook or the marks, moved out of the file, no longer
        reach pytest from it."""
        for name, more in (
                ('test_more', '\n\ndef test_more(page):\n    shoot(page, "more")\n'),
                ('TestMore', '\n\nclass TestMore:\n    pass\n'),
                ('pytest_runtest_setup',
                 '\n\ndef pytest_runtest_setup(item):\n    return item\n'),
                ('pytestmark', '\n\npytestmark = []\n'),
                ('__all__', '\n\n__all__ = []\n')):
            with self.subTest(name=name):
                self.assert_keeps(f'it deletes {name}, which pytest or Python reads',
                                  base=BASE_SHEET + more, helpers=MOVED_HELPERS + more)

    def test_deleting_what_is_no_definition_keeps_them(self):
        call = '\n\nexpect.set_options(timeout=5000)\n'
        self.assert_keeps('it deletes line 28, which is not a def, a class or a constant',
                          base=BASE_SHEET + call, helpers=MOVED_HELPERS + call)

    def test_a_deleted_assignment_of_no_one_name_keeps_them(self):
        for line in ('SPARE = OTHER = 1', 'SPARE, OTHER = 1, 2', 'SPARE: int',
                     'expect.x: int = 1'):
            with self.subTest(line=line):
                more = f'\n\n{line}\n'
                self.assert_keeps('it deletes line 28, which is not a def, a class or '
                                  'a constant', base=BASE_SHEET + more,
                                  helpers=MOVED_HELPERS + more)

    def test_a_moved_annotated_constant_takes_no_repeats(self):
        base = BASE_SHEET.replace('BOUND = ', 'BOUND: str = ')
        helpers = MOVED_HELPERS.replace('BOUND = ', 'BOUND: str = ')
        self.assertIn('ui.tests.browser.helpers', verdict(base=base, helpers=helpers))

    def test_a_deleted_fixture_still_named_keeps_them(self):
        """A fixture is read by its name, as a parameter or a string."""
        fixture = '\n\n@pytest.fixture\ndef sheet_page(page):\n    return page\n'
        for use in ('def test_sheet(page, sheet_page):',
                    '@pytest.mark.usefixtures("sheet_page")\ndef test_sheet(page):'):
            with self.subTest(use=use):
                self.assert_keeps(
                    'sheet_page is deleted and still used, but not imported',
                    base=('import pytest\n' + BASE_SHEET + fixture).replace(
                        'def test_sheet(page):', use),
                    sheet='import pytest\n' + MOVED_SHEET.replace(
                        'def test_sheet(page):', use),
                    helpers='import pytest\n' + MOVED_HELPERS + fixture)

    def test_a_moved_fixture_imported_by_name_does_not(self):
        fixture = '\n\n@pytest.fixture\ndef sheet_page(page):\n    return page\n'
        use = 'def test_sheet(page, sheet_page):'
        self.assertEqual(
            verdict(base=('import pytest\n' + BASE_SHEET + fixture).replace(
                        'def test_sheet(page):', use),
                    sheet=('import pytest\n' + MOVED_SHEET).replace(
                        'import bound, ready', 'import bound, ready, sheet_page').replace(
                        'def test_sheet(page):', use),
                    helpers='import pytest\n' + MOVED_HELPERS + fixture),
            {'ui.tests.browser.helpers': ['BOUND', 'bound', 'ready', 'sheet_page']})

    def test_a_copy_the_module_imports_from_elsewhere_keeps_them(self):
        """helpers.py's ready is conftest's, not the file's def."""
        helpers = MOVED_HELPERS.split('\n\ndef ready', 1)[0] + (
            '\nfrom ui.tests.browser.conftest import ready\n')
        self.assert_keeps('ui.tests.browser.helpers has ready otherwise than the file '
                          'had it', helpers=helpers)

    def test_moved_code_that_calls_itself_in_a_cycle_takes_no_repeats(self):
        """pong isn't imported: only the moved ping() reads it."""
        cycle = ('\n\ndef ping(n):\n    return pong(n - 1)\n'
                 '\n\ndef pong(n):\n    return n and ping(n)\n')
        self.assertEqual(
            verdict(base=BASE_SHEET.replace(TEST, cycle + TEST),
                    sheet=MOVED_SHEET.replace('import bound, ready',
                                              'import bound, ping, ready'),
                    helpers=MOVED_HELPERS + cycle),
            {'ui.tests.browser.helpers': ['BOUND', 'bound', 'ready', 'ping', 'pong']})

    def test_a_dependency_goes_where_its_reader_went(self):
        """conftest.py has the same copies and sorts first; BOUND goes with
        bound, which the file imports from helpers.py."""
        self.assertEqual(verdict(extra={CONFTEST: MOVED_HELPERS}),
                         {'ui.tests.browser.helpers': ['BOUND', 'bound', 'ready']})

    def test_only_imports_changed_keeps_them(self):
        """No definition moved, so no move: the imports regrouped only."""
        sheet = BASE_SHEET.replace('\n\nfrom ui.tests.browser.conftest',
                                   '\nfrom ui.tests.browser.conftest')
        self.assert_keeps('it moves no top-level definition', sheet=sheet)


# D101's stand-in: a browser test file with a fixture, a constant, two
# helpers (start() reads race()), tests, and a test class.
TIMER = 'ui/tests/browser/test_sheet.py'
D101_BASE = '''"""The sheet."""

import pytest
from playwright.sync_api import expect

LANES = 4


@pytest.fixture
def board(page):
    return page.locator("#board")


def race(page):
    for lane in range(1, LANES + 1):
        page.get_by_role("button", name=f"Stop lane {lane}").click()


def start(page):
    page.get_by_role("button", name="Start").click()
    race(page)


def test_races(page, board):
    start(page)
    expect(board).to_be_visible()


def test_waits(page):
    page.wait_for_timeout(10)
    expect(page.locator("body")).to_be_visible()


@pytest.mark.parametrize("n", [1, 2])
def test_counts(page, n):
    assert n


class TestBoard:
    """The board."""

    def setup_method(self):
        self.n = 1

    def test_one(self, page):
        assert self.n

    def test_two(self, page):
        start(page)
'''
# race()'s move to helpers.py, which D97 alone takes for no repeats.
D101_HELPERS = BASE_HELPERS + '''
LANES = 4


def race(page):
    for lane in range(1, LANES + 1):
        page.get_by_role("button", name=f"Stop lane {lane}").click()
'''


def d101(head, base=D101_BASE, helpers=D101_HELPERS):
    """changed_tests() on test_sheet.py from `base` to `head`, with
    helpers.py as `helpers`."""
    files = {**TREE, HELPERS: helpers}

    def module_text(name):
        rel = 'server/' + name.replace('.', '/')
        return files.get(f'{rel}.py', files.get(f'{rel}/__init__.py'))

    return TOOL.changed_tests(base, head, module_text)


def edit(old, new, text=D101_BASE):
    assert text.count(old) == 1, old
    return text.replace(old, new)


class ChangedTests(unittest.TestCase):
    """D101: a changed browser test file repeats the tests its diff adds or
    changes, by node id, and those that read a helper it adds or changes;
    anything else repeats the whole file (#336). In process, as #320's near
    misses are."""

    def assert_whole(self, why, head, **kw):
        with self.assertRaises(TOOL.WholeFile) as caught:
            d101(head, **kw)
        self.assertIn(why, str(caught.exception))

    def test_an_added_test(self):
        head = D101_BASE.replace('\n\nclass TestBoard', '\n\ndef test_more(page):\n'
                                 '    page.reload()\n\n\nclass TestBoard')
        self.assertEqual(d101(head), [('test_more', 'new')])

    def test_a_changed_test(self):
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)')
        self.assertEqual(d101(head), [('test_waits', 'changed')])

    def test_a_changed_parametrize_changes_its_test(self):
        head = edit('[1, 2]', '[1, 2, 3]')
        self.assertEqual(d101(head), [('test_counts', 'changed')])

    def test_a_changed_helper_and_its_callers(self):
        """race() changes; start() reads it, and test_races and
        TestBoard.test_two read start()."""
        head = edit('name=f"Stop lane {lane}"', 'name=f"Stop {lane}"')
        self.assertEqual(d101(head), [('test_races', 'reads start'),
                                      ('TestBoard::test_two', 'reads start')])

    def test_a_helper_drawn_out_of_a_test(self):
        """#323's shape: test_waits's lines become a helper, which a new test
        calls too."""
        head = edit('''def test_waits(page):
    page.wait_for_timeout(10)
    expect(page.locator("body")).to_be_visible()
''', '''def wait(page):
    page.wait_for_timeout(10)
    expect(page.locator("body")).to_be_visible()


def test_waits(page):
    wait(page)


def test_waits_again(page):
    page.reload()
    wait(page)
''')
        self.assertEqual(d101(head), [('test_waits', 'changed'),
                                      ('test_waits_again', 'new')])

    def test_a_helper_passed_by_reference_counts_as_read(self):
        """`page.route(url, hold)` never calls hold() in the file."""
        base = edit('    start(page)\n    expect(board)',
                    '    page.route("**", start)\n    expect(board)')
        head = edit('race(page)\n\n\ndef test_races', 'race(page)\n    page.reload()\n\n\n'
                    'def test_races', base)
        self.assertEqual(d101(head, base=base), [('test_races', 'reads start'),
                                                 ('TestBoard::test_two', 'reads start')])

    def test_a_helper_in_a_decorator_counts_as_read(self):
        base = edit('[1, 2]', 'start(None) or [1]')
        head = edit('race(page)\n\n\ndef test_races', 'race(page)\n    page.reload()\n\n\n'
                    'def test_races', base)
        self.assertIn(('test_counts', 'reads start'), d101(head, base=base))

    def test_a_deleted_helper_repeats_what_still_reads_it(self):
        head = edit('''def start(page):
    page.get_by_role("button", name="Start").click()
    race(page)


''', '')
        self.assertEqual(d101(head), [('test_races', 'reads start'),
                                      ('TestBoard::test_two', 'reads start')])

    def test_a_test_class_method(self):
        head = edit('assert self.n\n', 'assert self.n == 1\n')
        self.assertEqual(d101(head), [('TestBoard::test_one', 'changed')])

    def test_a_new_test_class_repeats_whole(self):
        head = D101_BASE + '\n\nclass TestMore:\n    def test_a(self):\n        pass\n'
        self.assertEqual(d101(head), [('TestMore', 'new')])

    def test_a_changed_test_beside_a_d97_move(self):
        """race() and LANES move to helpers.py unchanged: start(), which
        reads race(), changes nothing, so test_races doesn't repeat."""
        head = edit('''LANES = 4


@pytest''', '''from ui.tests.browser.helpers import race


@pytest''')
        head = edit('''def race(page):
    for lane in range(1, LANES + 1):
        page.get_by_role("button", name=f"Stop lane {lane}").click()


''', '', head)
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', head)
        self.assertEqual(d101(head), [('test_waits', 'changed')])
        changed = D101_HELPERS.replace('Stop lane', 'Stop')
        self.assert_whole('the import on line 6 is new or changed, and it is no '
                          'import-only move (D97: ui.tests.browser.helpers has race '
                          'otherwise than the file had it)', head, helpers=changed)

    def test_the_module_docstring_is_no_change(self):
        head = edit('"""The sheet."""', '"""The sheet, and more."""')
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', head)
        self.assertEqual(d101(head), [('test_waits', 'changed')])

    def test_a_class_docstring_is_no_change(self):
        head = edit('"""The board."""', '"""The board, and more."""')
        head = edit('assert self.n\n', 'assert self.n == 1\n', head)
        self.assertEqual(d101(head), [('TestBoard::test_one', 'changed')])

    def test_a_docstring_change_in_a_file_that_reads_doc(self):
        for name, read in (('module', '__doc__'), ('class', 'TestBoard.__doc__')):
            with self.subTest(name):
                base = edit('    assert n\n', f'    assert n and {read}\n')
                head = edit('"""The board."""' if name == 'class' else '"""The sheet."""',
                            '"""Changed."""', base)
                head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', head)
                self.assert_whole('a docstring changes, and the file reads `__doc__`',
                                  head, base=base)

    def test_a_new_test_method(self):
        head = D101_BASE + '\n    def test_three(self, page):\n        pass\n'
        self.assertEqual(d101(head), [('TestBoard::test_three', 'new')])

    def test_a_test_that_reads_its_class_is_not_picked_for_it(self):
        """TestBoard is a seed once test_two changes; test_one reads the name
        TestBoard and changes nothing, so test_three, which reads
        self.test_one, doesn't repeat either."""
        base = edit('assert self.n\n', 'assert isinstance(self, TestBoard)\n')
        base += '\n    def test_three(self, page):\n        self.test_one(page)\n'
        head = edit('        start(page)\n', '        start(page)\n        page.reload()\n', base)
        self.assertEqual(d101(head, base=base), [('TestBoard::test_two', 'changed')])

    def test_a_test_that_calls_a_test_that_reads_a_changed_helper(self):
        """#336 review, L1: test_more reads test_waits, which reads wait()."""
        base = edit('''def test_waits(page):
    page.wait_for_timeout(10)
''', '''def wait(page):
    page.wait_for_timeout(10)


def test_waits(page):
    wait(page)
''') + '\n\ndef test_more(page):\n    test_waits(page)\n'
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', base)
        self.assertEqual(d101(head, base=base), [('test_waits', 'reads wait'),
                                                 ('test_more', 'reads test_waits')])

    def test_a_method_that_calls_a_changed_method_through_self(self):
        """#336 review, L2: test_two reads self.test_one, and test_three reads
        test_two through the class's name."""
        base = edit('        start(page)\n', '        self.test_one(page)\n')
        base += '\n    def test_three(self, page):\n        TestBoard.test_two(self, page)\n'
        head = edit('assert self.n\n', 'assert self.n == 1\n', base)
        self.assertEqual(d101(head, base=base), [
            ('TestBoard::test_one', 'changed'),
            ('TestBoard::test_two', 'reads test_one of TestBoard'),
            ('TestBoard::test_three', 'reads test_two of TestBoard')])

    def test_methods_through_self_near_misses(self):
        """test_two reads test_one through self, and itself; test_three reads
        the unchanged test_four through self; test_four reads `test_one` off
        `page`, which is no class, and `self.n.real`."""
        base = edit('        start(page)\n', '        self.test_one(page)\n'
                    '        if page is None:\n            self.test_two(None)\n')
        base += ('\n    def test_three(self, page):\n        self.test_four(page)\n\n'
                 '    def test_four(self, page):\n'
                 '        assert page.test_one is None and self.n.real\n')
        head = edit('assert self.n\n', 'assert self.n == 1\n', base)
        self.assertEqual(d101(head, base=base), [
            ('TestBoard::test_one', 'changed'),
            ('TestBoard::test_two', 'reads test_one of TestBoard')])

    def test_a_method_that_calls_through_self_a_method_that_reads_a_changed_helper(self):
        """test_one reads self.test_two, which reads start(), which reads the
        changed race()."""
        base = edit('        assert self.n\n', '        self.test_two(page)\n')
        head = edit('name=f"Stop lane {lane}"', 'name=f"Stop {lane}"', base)
        self.assertEqual(d101(head, base=base), [
            ('test_races', 'reads start'),
            ('TestBoard::test_one', 'reads test_two of TestBoard'),
            ('TestBoard::test_two', 'reads start')])

    def test_a_subclass_of_a_class_whose_method_reads_a_changed_helper(self):
        base = D101_BASE + '\n\nclass TestMore(TestBoard):\n    pass\n'
        head = edit('name=f"Stop lane {lane}"', 'name=f"Stop {lane}"', base)
        self.assert_whole('the class TestMore reads TestBoard', head, base=base)

    def test_a_test_that_reads_a_class_whose_method_reads_a_changed_helper(self):
        """TestBoard is reached through test_two's start(), and test_more,
        unchanged, reads TestBoard, and test_last reads test_more: the
        module's reach runs again. TestQuiet reaches nothing, so
        test_quiet, which reads it, doesn't repeat."""
        base = D101_BASE + ('\n\ndef test_more(page):\n    assert TestBoard\n'
                            '\n\ndef test_last(page):\n    test_more(page)\n'
                            '\n\nclass TestQuiet:\n    def test_q(self):\n        pass\n'
                            '\n\ndef test_quiet(page):\n    assert TestQuiet\n')
        head = edit('name=f"Stop lane {lane}"', 'name=f"Stop {lane}"', base)
        self.assertEqual(d101(head, base=base), [
            ('test_races', 'reads start'), ('TestBoard::test_two', 'reads start'),
            ('test_more', 'reads TestBoard'), ('test_last', 'reads test_more')])

    def test_a_class_helper_method_that_calls_a_method_that_reads_a_changed_helper(self):
        base = edit('        self.n = 1\n', '        self.n = 1\n\n    def again(self, page):\n'
                    '        self.test_two(page)\n')
        base += '\n    def test_three(self, page):\n        self.again(page)\n'
        head = edit('name=f"Stop lane {lane}"', 'name=f"Stop {lane}"', base)
        self.assert_whole('the class TestBoard reads test_two outside its test methods',
                          head, base=base)

    def test_a_method_that_calls_a_deleted_method_through_self(self):
        base = edit('        start(page)\n', '        self.test_one(page)\n')
        head = edit('''    def test_one(self, page):
        assert self.n

''', '', base)
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', head)
        self.assertEqual(d101(head, base=base), [
            ('test_waits', 'changed'), ('TestBoard::test_two', 'reads test_one of TestBoard')])

    def test_a_class_helper_method_that_calls_a_changed_test(self):
        base = edit('        self.n = 1\n', '        self.n = 1\n\n    def again(self, page):\n'
                    '        self.test_one(page)\n')
        head = edit('assert self.n\n', 'assert self.n == 1\n', base)
        self.assert_whole('the class TestBoard reads test_one outside its test methods',
                          head, base=base)

    def test_a_deleted_test_another_reads(self):
        base = D101_BASE + '\n\ndef test_more(page):\n    test_waits(page)\n'
        head = edit('''def test_waits(page):
    page.wait_for_timeout(10)
    expect(page.locator("body")).to_be_visible()


''', '', base)
        self.assertEqual(d101(head, base=base), [('test_more', 'reads test_waits')])

    def test_a_deleted_test_class_a_test_reads(self):
        base = D101_BASE + '\n\nclass TestOld:\n    def test_a(self):\n        pass\n'
        base = edit('    assert n\n', '    assert n and TestOld\n', base)
        head = base.split('\n\nclass TestOld:')[0] + '\n'
        self.assertEqual(d101(head, base=base), [('test_counts', 'reads TestOld')])

    def test_a_deleted_method_of_a_subclassed_test_class(self):
        base = D101_BASE + '\n\nclass TestMore(TestBoard):\n    pass\n'
        head = edit('''    def test_one(self, page):
        assert self.n

''', '', base)
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', head)
        self.assert_whole('the class TestMore reads TestBoard', head, base=base)

    def test_a_changed_helper_only_a_constant_reads(self):
        base = edit('LANES = 4', 'LANES = 4\nSTEPS = [lambda: shout()]')
        base += '\n\ndef shout():\n    return 1\n'
        head = edit('    return 1\n', '    return 2\n', base)
        self.assert_whole('the module-level STEPS reads shout', head, base=base)

    def test_a_changed_helper_only_it_reads(self):
        base = D101_BASE + '\n\ndef spare(n):\n    return spare(n - 1) if n else 0\n'
        head = edit('else 0\n', 'else 1\n', base)
        self.assert_whole('it adds or changes the helper spare, which nothing in the file '
                          'reads', head, base=base)

    def test_a_file_that_reads_doc_with_no_docstring_change(self):
        base = edit('    assert n\n', '    assert n and __doc__\n')
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', base)
        self.assertEqual(d101(head, base=base), [('test_waits', 'changed')])

    def test_a_docstring_change_where_only_head_reads_doc(self):
        head = edit('    assert n\n', '    assert n and __doc__\n')
        head = edit('"""The sheet."""', '"""Changed."""', head)
        self.assert_whole('a docstring changes, and the file reads `__doc__`', head)

    def test_a_class_docstring_read_by_getattr(self):
        base = edit('    assert n\n', '    assert n and getattr(TestBoard, "__doc__")\n')
        head = edit('"""The board."""', '"""Changed."""', base)
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', head)
        self.assert_whole('a docstring changes, and the file reads `__doc__`', head,
                          base=base)

    def test_the_d97_check_drops_docstrings_and_deleted_tests(self):
        """Beside race()'s move: the module's docstring changes and a test is
        deleted, which D97 alone would refuse."""
        head = edit('''LANES = 4


@pytest''', '''from ui.tests.browser.helpers import race


@pytest''')
        head = edit('''def race(page):
    for lane in range(1, LANES + 1):
        page.get_by_role("button", name=f"Stop lane {lane}").click()


''', '', head)
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', head)
        head = edit('"""The sheet."""', '"""Changed."""', head)
        head = edit('''@pytest.mark.parametrize("n", [1, 2])
def test_counts(page, n):
    assert n


''', '', head)
        self.assertEqual(d101(head), [('test_waits', 'changed')])

    def test_each_xunit_name_is_a_hook(self):
        """Each name pytest calls by its presence (XUNIT, swept from pytest
        by server/tests/test_xunit_names.py) falls back, at module level and
        in a test class, as a hook (#336 review, M1)."""
        self.assertIn('setUpModule', TOOL.XUNIT)
        for name in TOOL.XUNIT:
            with self.subTest(name):
                base = D101_BASE + f'\n\ndef {name}():\n    pass\n'
                head = D101_BASE + f'\n\ndef {name}():\n    print()\n'
                self.assert_whole(f'the hook {name} is new or changed', head, base=base)
                base = edit('    def setup_method(self):\n',
                            f'    def {name}(self):\n        pass\n\n'
                            '    def setup_method(self):\n')
                head = edit('        pass\n\n    def setup_method', '        print()\n\n'
                            '    def setup_method', base)
                self.assert_whole('the class TestBoard changes outside its test methods: '
                                  f'the hook {name}', head, base=base)

    def test_a_helper_named_like_no_xunit_name_is_a_helper(self):
        base = edit('def start(page):', 'def setup_board(page):')
        base = base.replace('    start(page)\n', '    setup_board(page)\n')
        head = edit('name="Start").click()', 'name="Go").click()', base)
        self.assertEqual(d101(head, base=base), [('test_races', 'reads setup_board'),
                                                 ('TestBoard::test_two', 'reads setup_board')])

    def test_a_changed_helper_class(self):
        base = D101_BASE + '\n\nclass Board:\n    def test_a(self):\n        pass\n'
        head = edit('        pass\n', '        print()\n', base)
        self.assert_whole('the class Board is new or changed', head, base=base)

    def test_a_test_class_with_an_init_is_no_test_class(self):
        """pytest collects no class with an `__init__`."""
        base = D101_BASE + ('\n\nclass TestHelper:\n    def __init__(self):\n        pass\n\n'
                            '    def test_a(self):\n        pass\n')
        head = edit('    def test_a(self):\n        pass\n',
                    '    def test_a(self):\n        print()\n', base)
        self.assert_whole('the class TestHelper is new or changed', head, base=base)

    def test_a_non_string_first_statement_is_no_docstring(self):
        base = 'b"x"\n' + D101_BASE
        head = edit('wait_for_timeout(10)', 'wait_for_timeout(20)', 'b"y"\n' + D101_BASE)
        self.assert_whole('the module-level code on line 1 is new or changed', head,
                          base=base)

    def test_a_relative_import(self):
        head = edit('import pytest\n', 'import pytest\nfrom . import conftest\n')
        self.assert_whole('the file line 4: a relative import', head)

    def test_a_test_method_defined_twice(self):
        head = edit('    def test_two(self, page):\n',
                    '    def test_two(self, page):\n        pass\n\n'
                    '    def test_two(self, page):\n')
        self.assert_whole('the file defines TestBoard.test_two twice', head)

    # Each case below falls back to the whole file.

    def test_a_changed_fixture(self):
        self.assert_whole('the fixture board is new or changed',
                          edit('locator("#board")', 'locator("#sheet")'))
        base = edit('import pytest\n', 'import pytest\nfrom pytest import fixture\n')
        base = edit('@pytest.fixture\n', '@fixture\n', base)
        self.assert_whole('the fixture board is new or changed',
                          edit('locator("#board")', 'locator("#sheet")', base), base=base)

    def test_a_changed_constant(self):
        self.assert_whole('the module-level LANES is new or changed',
                          edit('LANES = 4', 'LANES = 6'))

    def test_a_changed_pytestmark(self):
        head = edit('LANES = 4', 'LANES = 4\npytestmark = pytest.mark.browser')
        self.assert_whole('the module-level pytestmark is new or changed', head)

    def test_an_import_added_for_a_new_test(self):
        head = edit('import pytest\n', 'import pytest\nimport re\n')
        head += '\n\ndef test_more(page):\n    assert re.escape("a")\n'
        self.assert_whole('the import on line 4 is new or changed, and it is no '
                          'import-only move (D97: it moves no top-level definition)',
                          head)

    def test_a_changed_setup_function_is_a_hook(self):
        """pytest calls setup_function by its name: no helper (#336 lead)."""
        base = D101_BASE + '\n\ndef setup_function():\n    pass\n'
        head = D101_BASE + '\n\ndef setup_function():\n    print()\n'
        self.assert_whole('the hook setup_function is new or changed', head, base=base)

    def test_a_class_outside_its_test_methods(self):
        self.assert_whole('the class TestBoard changes outside its test methods: the '
                          'hook setup_method', edit('self.n = 1', 'self.n = 2'))

    def test_a_subclass_of_a_changed_test_class(self):
        base = D101_BASE + '\n\nclass TestMore(TestBoard):\n    pass\n'
        head = edit('assert self.n\n', 'assert self.n == 1\n', base)
        self.assert_whole('the class TestMore reads TestBoard', head, base=base)

    def test_a_changed_helper_a_fixture_reads(self):
        base = edit('return page.locator("#board")', 'return start(page)')
        head = edit('race(page)\n\n\ndef test_races', 'race(page)\n    page.reload()\n\n\n'
                    'def test_races', base)
        self.assert_whole('the fixture board reads start', head, base=base)

    def test_a_changed_helper_a_constant_reads(self):
        base = edit('LANES = 4', 'LANES = 4\nSTEPS = [race]')
        head = edit('name=f"Stop lane {lane}"', 'name=f"Stop {lane}"', base)
        self.assert_whole('the module-level STEPS reads race', head, base=base)

    def test_a_changed_helper_nothing_reads(self):
        head = D101_BASE + '\n\ndef spare(page):\n    return page\n'
        self.assert_whole('it adds or changes the helper spare, which nothing in the '
                          'file reads', head)

    def test_a_test_decorated_otherwise(self):
        """Only `pytest.mark.X` and its calls are a test's marks."""
        for decorator in ('slow', 'unittest.mock.patch("os.sep", "/")', 'other.mark.slow',
                          'a.pytest.mark.slow', 'pytest.fixture', 'pytest.mark',
                          'pytest.helpers.slow'):
            with self.subTest(decorator):
                base = edit('@pytest.mark.parametrize("n", [1, 2])\n',
                            f'@pytest.mark.parametrize("n", [1, 2])\n@{decorator}\n')
                head = edit('assert n\n', 'assert n > 0\n', base)
                self.assert_whole('test_counts is new or changed', head, base=base)

    def test_a_name_bound_twice(self):
        head = D101_BASE + '\n\ndef test_waits(page):\n    pass\n'
        self.assert_whole('the file binds test_waits more than once', head)

    def test_a_global(self):
        """`nonlocal` binds no module name, so only `global` falls back."""
        nonlocal_ = D101_BASE + ('\n\ndef outer():\n    x = 1\n\n    def inner():\n'
                                 '        nonlocal x\n\n    return inner\n\n\n'
                                 'def test_more():\n    outer()\n')
        self.assertEqual(d101(nonlocal_), [('test_more', 'new')])
        head = edit('    assert n\n', '    global LANES\n    assert n\n')
        self.assert_whole("the file line 36: `global` lets a def rebind a module name",
                          head)

    def test_an_assignment_expression_outside_a_function(self):
        head = edit('[1, 2]', '[(k := 1), 2]')
        self.assert_whole('an assignment expression (:=) outside a function', head)

    def test_a_comment_only_change(self):
        head = edit('    start(page)\n    expect', '    start(page)  # go\n    expect')
        self.assert_whole('by AST it adds or changes no test', head)

    def test_a_deleted_test_alone(self):
        head = edit('''def test_waits(page):
    page.wait_for_timeout(10)
    expect(page.locator("body")).to_be_visible()


''', '')
        self.assert_whole('by AST it adds or changes no test', head)

    def test_a_file_that_does_not_parse(self):
        self.assert_whole('cannot be parsed', D101_BASE + '\ndef broken(:\n')

    def test_a_file_new_on_the_branch(self):
        self.assert_whole('new on the branch', D101_BASE, base=None)


# The readers a reach shape chains (#336 retro): a module helper, a module
# test, a test method of TestK, and a TestK method that is no test.
REACH_KINDS = ('helper', 'test', 'method', 'again')
IN_CLASS = {'method', 'again'}


def reach_shape(leaf, chain, subclass, value):
    """A test file in which `chain`'s readers each call the one before,
    the first calling `leaf`, which returns `value`: a module-level reader
    calls a TestK method as `TestK().name()`, a TestK method as
    `self.name()`. With `subclass`, TestSub(TestK) inherits TestK's tests.
    Returns the text and the leaf's name."""
    kinds = [leaf, *chain]
    names = [{'helper': f'h{i}', 'test': f'test_t{i}', 'method': f'test_m{i}',
              'again': f'again{i}'}[k] for i, k in enumerate(kinds)]
    module, klass = [], ['    def test_z(self):\n        return 0\n']
    for i, (kind, name) in enumerate(zip(kinds, names)):
        if i == 0:
            body = str(value)
        elif kinds[i - 1] not in IN_CLASS:
            body = f'{names[i - 1]}()'
        elif kind in IN_CLASS:
            body = f'self.{names[i - 1]}()'
        else:
            body = f'TestK().{names[i - 1]}()'
        if kind in IN_CLASS:
            klass.append(f'    def {name}(self):\n        return {body}\n')
        else:
            module.append(f'def {name}():\n    return {body}\n')
    text = 'def test_z0():\n    return 0\n\n\n' + '\n\n'.join(module)
    text += '\n\nclass TestK:\n' + '\n'.join(klass)
    if subclass:
        text += '\n\nclass TestSub(TestK):\n    pass\n'
    return text, names[0]


def tests_that_call(text, leaf):
    """The node ids of the tests pytest would collect from `text` whose run
    calls `leaf`, found by running each one under a profiler: the expected
    set comes from Python, not from the tool's reading of the AST."""
    ns = {}
    exec(compile(text, '<reach>', 'exec'), ns)
    tests = [(n, f) for n, f in ns.items() if n.startswith('test') and callable(f)]
    for cname, cls in ns.items():
        if cname.startswith('Test') and isinstance(cls, type):
            tests += [(f'{cname}::{m}', getattr(cls(), m)) for m in dir(cls)
                      if m.startswith('test')]
    out = set()
    for key, run in tests:
        called = []

        def profile(frame, event, arg):
            if event == 'call' and frame.f_code.co_filename == '<reach>' \
                    and frame.f_code.co_name == leaf:
                called.append(key)

        sys.setprofile(profile)
        try:
            run()
        finally:
            sys.setprofile(None)
        if called:
            out.add(key)
    return out


def reach_shapes():
    """Each leaf a diff changes (a helper, a test, a test method), read
    through 0, 1 or 2 readers of each kind and then a test or a test
    method, with and without a subclass."""
    for leaf in ('helper', 'test', 'method'):
        for hops in (0, 1, 2):
            for middle in itertools.product(REACH_KINDS, repeat=hops):
                for end in ('test', 'method'):
                    for subclass in (False, True):
                        yield leaf, (*middle, end), subclass


class ReachShapes(unittest.TestCase):
    """D101's reach against every chain of readers up to three long (#336
    retro). Review found a test that calls a test (L1) and a method that
    calls one through `self` (L2), and the advisor's probe then found a
    test method reached through a helper missing from its class's reach,
    a subclass, and a class helper method: each was probed one shape at a
    time. Here each shape's expected tests are the ones that call the
    changed leaf when run: a test left out fails open, and a test picked
    that never calls it repeats for nothing."""

    maxDiff = None

    def test_the_tests_that_call_the_changed_leaf_repeat_and_no_other(self):
        misses, resolved = [], set()
        shapes = list(reach_shapes())
        for leaf, chain, subclass in shapes:
            base, _ = reach_shape(leaf, chain, subclass, 1)
            head, name = reach_shape(leaf, chain, subclass, 2)
            try:
                picked = {key for key, _ in TOOL.changed_tests(base, head, lambda m: None)}
            except TOOL.WholeFile:
                continue  # the whole file repeats, which holds every test
            resolved.add((leaf, chain, subclass))
            expected = tests_that_call(head, name)
            if picked != expected:
                misses.append(f'{leaf} <- {" <- ".join(chain)}'
                              f'{" + TestSub" if subclass else ""}: missing '
                              f'{sorted(expected - picked)}, extra {sorted(picked - expected)}')
        self.assertEqual(misses, [])
        # A tool that repeated every file whole would pass the loop above:
        # each shape with no subclass and no class helper method resolves.
        plain = {s for s in shapes if not s[2] and 'again' not in s[1]}
        self.assertEqual(sorted(plain - resolved), [])
        longest = max(len(chain) for _, chain, _ in shapes)
        self.assertEqual((len(shapes), len(plain), longest), (252, 78, 3))


def lines(head, base=D101_BASE, postgres=False):
    files = {**TREE, HELPERS: D101_HELPERS}

    def module_text(name):
        rel = 'server/' + name.replace('.', '/')
        return files.get(f'{rel}.py', files.get(f'{rel}/__init__.py'))

    return '\n'.join(TOOL.load_repeats(TIMER, base, head, module_text, postgres)) + '\n'


class LoadRepeats(unittest.TestCase):
    """The lines each changed browser test file prints (#336): one command
    of its node ids with today's count, or the whole file and why."""

    def test_node_ids_in_one_command_with_the_flake_ticket_rule(self):
        head = edit('name=f"Stop lane {lane}"', 'name=f"Stop {lane}"')
        out = lines(head)
        self.assertIn(f'  {TIMER}: 25 repeats, involves timing (line 30: wait_for_timeout)\n'
                      '      not an import-only move (D97): line 14 is new or changed\n'
                      '      the tests it adds or changes, and those that read a helper '
                      'it adds or changes (D101):\n'
                      '        test_races: reads start\n'
                      '        TestBoard::test_two: reads start\n'
                      f'      a flake ticket repeats the whole file {TIMER} 25 times '
                      'instead, on the database it failed on (D101)\n'
                      '      tools/pytest-under-load.sh -m browser -n 4 --count 25 '
                      f'{TIMER}::test_races {TIMER}::TestBoard::test_two\n', out)

    def test_a_deterministic_file_keeps_5_per_font(self):
        base = D101_BASE.replace('page.wait_for_timeout(10)', 'page.reload()') \
            .replace('locator("#board")', 'locator("#board").bounding_box()')
        head = edit('assert self.n\n', 'assert self.n == 1\n', base)
        out = lines(head, base=base, postgres=True)
        run = (f'tools/pytest-under-load.sh --postgres -m browser -n 4 --count 5 '
               f'{TIMER}::TestBoard::test_one\n')
        self.assertIn(f'{TIMER}: 5 repeats, deterministic', out)
        self.assertIn(f'      {run}', out)
        self.assertIn(f'      DERBYNET_BROWSER_FONT="DejaVu Sans" {run}', out)

    def test_a_fallback_prints_the_whole_file_and_why(self):
        out = lines(edit('LANES = 4', 'LANES = 6'))
        self.assertIn('      the whole file (D101): the module-level LANES is new or '
                      'changed, and it is no import-only move (D97: line 6 is new or '
                      'changed)\n'
                      '      tools/pytest-under-load.sh -m browser -n 4 --count 25 '
                      f'{TIMER}\n', out)
        self.assertNotIn('a flake ticket', out)

    def test_each_fallback_case_prints_the_whole_file_command(self):
        cases = {
            'fixture': edit('locator("#board")', 'locator("#sheet")'),
            'constant': edit('LANES = 4', 'LANES = 6'),
            'class': edit('self.n = 1', 'self.n = 2'),
            'parse': D101_BASE + '\ndef broken(:\n',
            'hook': D101_BASE + '\n\ndef setup_module():\n    pass\n',
            'pytestmark': edit('LANES = 4', 'LANES = 4\npytestmark = pytest.mark.browser'),
            'import': edit('import pytest\n', 'import pytest\nimport re\n')
            + '\n\ndef test_more(page):\n    assert re.escape("a")\n',
        }
        for name, head in cases.items():
            with self.subTest(name):
                out = lines(head)
                self.assertIn('      the whole file (D101): ', out)
                self.assertIn(f'--count 25 {TIMER}\n', out)
        out = lines(D101_BASE, base=TOOL.NO_BASE)
        self.assertIn('the whole file (D101): --stdin has no BASE to compare with\n', out)
        self.assertIn(f'--count 25 {TIMER}\n', out)
        self.assertNotIn('import-only (D97)', out)

    def test_an_import_only_move_prints_none(self):
        head = edit('''LANES = 4


@pytest''', '''from ui.tests.browser.helpers import race


@pytest''')
        head = edit('''def race(page):
    for lane in range(1, LANES + 1):
        page.get_by_role("button", name=f"Stop lane {lane}").click()


''', '', head)
        out = lines(head)
        self.assertIn(f'  {TIMER}: import-only (D97): none\n', out)
        self.assertNotIn('pytest-under-load.sh', out)


# Each way a module statement binds a name, run through Python itself
# (plan/AGENT-BRIEF.md: a rule that mirrors a parser is built from a sweep).
# #320's bindings() missed a module-level walrus until review (H4).
BINDING_FORMS = {
    'assign': 'a = 1', 'unpack': 'a, *b = 1, 2, 3', 'chained': 'a = b = 1',
    'augmented': 'a = 0\na += 1', 'annotated': 'a: int = 1',
    'for': 'for a in [1]:\n    pass\nelse:\n    b = 1',
    'while': 'while not globals().get("a"):\n    a = 1', 'if': 'if True:\n    a = 1',
    'with': 'import contextlib\nwith contextlib.nullcontext(1) as a:\n    b = 1',
    'try': 'try:\n    raise ValueError\nexcept ValueError as e:\n    a = 1\nfinally:\n    b = 1',
    'import': 'import os.path', 'import as': 'import os as a',
    'from': 'from os import path, sep as a',
    'def': 'def a():\n    pass', 'async def': 'async def a():\n    pass',
    'class': 'class a:\n    b = 1',
    'decorated': 'import functools\n@functools.cache\ndef a():\n    pass',
    'match': 'match [1, {}]:\n    case [int() as a, {**b}, *c]:\n        pass\n'
             '    case d:\n        pass',
    'type alias': 'type a = int',
    'global': 'def f():\n    global a\n    a = 1\n\n\nf()',
    'walrus': 'x = (a := 1)', 'walrus in a call': 'print((a := 1))',
    'walrus in a comprehension': 'x = [(a := i) for i in [1]]',
    'walrus in a default': 'def f(x=(a := 1)):\n    pass',
    'walrus in a lambda default': 'f = lambda x=(a := 1): x',
    'walrus in a decorator': 'def d(f):\n    return f\n\n\n@(b := d)\ndef f():\n    pass',
    'walrus in a class keyword': 'class C(metaclass=(m := type)):\n    pass',
    'walrus in an f-string': 'x = f"{(a := 1)}"', 'walrus in an assert': 'assert (a := 1)',
}


class BindingSweep(unittest.TestCase):
    def test_each_binding_form_is_seen_or_refused(self):
        """Each name a form binds when Python runs it is a key of bindings(),
        or no_walrus() refuses the module; a miss would let a moved name's
        twin pass as the same object (D97), or a test or helper bound again
        pass as one (D101)."""
        for label, source in BINDING_FORMS.items():
            with self.subTest(label):
                made = {}
                with contextlib.redirect_stdout(io.StringIO()):
                    exec(compile(source, label, 'exec'), made)
                made = {n for n in made if not (n.startswith('__') and n.endswith('__'))}
                tree = ast.parse(source)
                try:
                    TOOL.no_walrus(tree, label)
                except TOOL.NotImportOnly:
                    continue
                self.assertEqual(made - set(TOOL.bindings(tree, label)), set())


class RealTree(unittest.TestCase):
    """Every page file of the real tree reaches a test by a rule, not the
    fallback: a new page, template or script name that no test file is named
    after shows up here."""

    def test_every_static_file_template_and_ui_package_maps(self):
        """Every template, not one per folder: #288's sample took the first
        file a folder listed, which is base.html on ext4 and placeholder.html
        on tmpfs, so a fresh worktree under /tmp failed where the hook passed
        (#312 retro)."""
        server = REPO / 'server'
        files = [str(p.relative_to(REPO)) for p in (server / 'ui/static/ui').glob('*.*')
                 if p.suffix in ('.css', '.js')]
        files += sorted(str(p.relative_to(REPO))
                        for p in (server / 'ui/templates').rglob('*.html'))
        files += [str(p.relative_to(REPO)) for p in (server / 'ui').glob('*/views.py')]
        out = run(*files, root=REPO)
        for path in files:
            with self.subTest(path=path):
                block = section(out, path)
                self.assertNotIn('[fallback]', block)
                named = [ln.strip() for ln in block.splitlines()
                         if ln.startswith(' ' * 6)]
                page_wide = {f'ui/tests/{n}' for n in (
                    'test_navigation.py', 'test_every_view.py',
                    'test_query_growth.py', 'test_photo_urls.py')}
                self.assertTrue(set(named) - page_wide,
                                'no test target beyond the page-wide checks')

    def test_each_file_the_role_pages_check_reads_prints_it(self):
        """The fixtures, the seeded roles, the gates, the URL confs that
        resolve() reads, and each view its OTHER_PAGES names, read from the
        real test: #323's CI failed on that fixture (#320 retro)."""
        fixture = 'tests/test_role_pages_fixture.py'
        views = [f'server/{v}' for v in TOOL.role_page_views(TOOL.Tree(REPO))]
        self.assertIn('server/ui/timer/views.py', views)
        self.assertEqual(sorted(set(views)), sorted(views))
        self.assertEqual([v for v in views if not (REPO / v).is_file()], [])
        files = ['testing/fixtures/role-pages.json', 'testing/fixtures/role-pages-django.json',
                 'server/accounts/defaults.py', 'server/accounts/permissions.py',
                 'server/ui/redirects.py', 'server/ui/navigation.py',
                 'server/derbynet/urls.py', 'server/ui/urls.py', *views]
        out = run(*files, 'server/awards/editing.py', root=REPO)
        for path in files:
            with self.subTest(path=path):
                self.assertIn(fixture, section(out, path))
        self.assertIn('[role-gates] a URL conf', section(out, 'server/ui/urls.py'))
        self.assertIn('OTHER_PAGES', section(out, 'server/ui/timer/views.py'))
        self.assertNotIn('[role-gates]', section(out, 'server/awards/editing.py'))

    def test_every_command_and_server_tool_maps(self):
        server = REPO / 'server'
        files = [str(p.relative_to(REPO)) for p in server.glob('*/management/commands/*.py')
                 if p.stem != '__init__']
        files += [str(p.relative_to(REPO)) for p in (server / 'tools').rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts]
        out = run(*files, root=REPO)
        for path in files:
            with self.subTest(path=path):
                self.assertNotIn('[fallback]', section(out, path))


if __name__ == '__main__':
    unittest.main()
