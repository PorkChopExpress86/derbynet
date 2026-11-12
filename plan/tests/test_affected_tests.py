"""plan/bin/affected-tests against a small stand-in of the repository, so each
mapping rule has a test that fails when the rule is removed, and against the
real tree, so every page file maps to a test without the fallback.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


class Everything(unittest.TestCase):
    def test_pyproject_runs_every_suite_on_both_databases(self):
        out = run('server/pyproject.toml')
        self.assertIn('[everything]', out)
        self.assertIn('Run everything, on both databases', out)
        self.assertIn('  tools/pytest-postgres.sh -n auto -m browser ui', out)

    def test_a_conftest_runs_everything(self):
        out = run('server/ui/tests/browser/conftest.py')
        self.assertIn('[everything] a shared conftest', out)

    def test_a_workflow_runs_everything(self):
        out = run('.github/workflows/server.yml')
        self.assertIn('[everything] a CI workflow', out)


class Docs(unittest.TestCase):
    def test_markdown_maps_to_no_target_and_says_why(self):
        out = run('plan/README.md')
        self.assertIn('[docs] docs only', out)
        self.assertNotIn('[fallback]', out)
        self.assertIn('(no pytest target', out)


class Website(unittest.TestCase):
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
    def test_a_plan_tool_runs_plan_tests(self):
        out = run('plan/bin/pr-merge')
        self.assertIn('[plan-tools]', out)
        self.assertIn('python3 -I -m unittest discover -s ../plan/tests', commands(out))

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

    def test_a_helper_the_root_conftest_imports_runs_everything(self):
        out = run('server/racing/engine/tests/golden.py')
        self.assertIn('the root conftest imports it', out)


class TestData(unittest.TestCase):
    def test_a_fixture_maps_to_the_test_that_names_it(self):
        out = run('server/racing/engine/tests/data/tables.json')
        self.assertIn('[test-data]', out)
        self.assertIn('racing/engine/tests/test_tables.py', commands(out))


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

    def test_an_unmapped_file_outside_server_runs_everything(self):
        out = run('.gitattributes')
        self.assertIn('[fallback] no rule maps it and it is outside server/', out)
        self.assertIn('Run everything', out)


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


class RealTree(unittest.TestCase):
    """Every page file of the real tree reaches a test by a rule, not the
    fallback: a new page folder or script name that no test file is named
    after shows up here."""

    def test_every_static_file_template_folder_and_ui_package_maps(self):
        server = REPO / 'server'
        files = [str(p.relative_to(REPO)) for p in (server / 'ui/static/ui').glob('*.*')
                 if p.suffix in ('.css', '.js')]
        files += sorted({str(next(p.parent.glob('*.html')).relative_to(REPO))
                         for p in (server / 'ui/templates').rglob('*.html')})
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
