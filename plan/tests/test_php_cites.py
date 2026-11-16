"""plan/php_cites.py's rule, and plan/owner-decisions.md held to it (#346).

A decision that says what a PHP file does cites the line: D98 first said
PHP's `fake-timer.php` links the dashboard and the timer test, where
`fake-timer.php:99-100` links `coordinator.php` and `timer.php`.
plan/lint-issues.py applies the same rule to open issue bodies
(test_lint_issues.py's PhpCites).

Run: python3 -I -m unittest discover -s plan/tests
"""
import importlib.util
import re
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

PLAN = Path(__file__).resolve().parent.parent
DECISIONS = PLAN / 'owner-decisions.md'
_spec = importlib.util.spec_from_file_location('php_cites', PLAN / 'php_cites.py')
php_cites = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(php_cites)

# D98's row as d9438939 (#325) first recorded it, and as 64cdde2e rewrote it.
D98_FIRST = (
    '| D98 | 2026-11-14 | The fake timer page shows its intro links to the race dashboard '
    'and the timer test only to a role that can open them, and the words as plain text '
    "otherwise, where PHP's `fake-timer.php` links them for every role. Since D96 the Timer "
    'role opens the page, and both pages need `CONTROL_RACE`, which Timer lacks. | #324 |')
D98_NOW = (
    '| D98 | 2026-11-14 | The fake timer page shows its intro links to the race dashboard '
    'and the timer test only to a role that can open them, and the words as plain text '
    "otherwise. PHP's `fake-timer.php:99-100` links its own targets, `coordinator.php` "
    '("Racing") and `timer.php` ("Simulated Racing"), for every role; #50 points the port\'s '
    'text at the race dashboard and the timer test. Since D96 the Timer role opens the page, '
    'and both pages need `CONTROL_RACE`, which Timer lacks. | #324 |')

# Rows that predate the rule (#346), keyed by decision and the claim's words.
# Remove an entry when its row gains a cite; never add one.
EXEMPT = {
    ('D80', "`settings.php`'s input has"),
    ('D84', "`scenes.php`'s previews ran"),
}


# The verbs #346's Scope gives as examples.
TICKET_VERBS = ('links', 'shows', 'stores', 'writes', 'answers', 'sets', 'refuses', 'calls')


def claims(text):
    return [words for _, words, _ in php_cites.uncited(text)]


def decision_claims(text):
    """`{(decision, claim words)}` for each uncited claim in a decisions
    table; a line outside a row is keyed by its line number."""
    lines = text.splitlines()
    found = set()
    for number, words, _ in php_cites.uncited(text):
        row = re.match(r'\| (D\d+) \|', lines[number - 1])
        found.add((row[1] if row else f'line {number}', words))
    return found


def check(text, exempt):
    """`(uncited, stale)`: the claims `exempt` leaves out, and the entries
    of `exempt` that name no uncited claim."""
    found = decision_claims(text)
    return sorted(found - exempt), sorted(exempt - found)


class Rule(unittest.TestCase):
    def test_a_claim_with_a_cite_passes(self):
        self.assertEqual(claims('`vote.php:67` shows the ballot.'), [])
        self.assertEqual(claims('`vote.php` shows the ballot (`vote.php:67`).'), [])
        self.assertEqual(claims('vote.php shows the ballot, `vote.php` line 67.'), [])

    def test_a_claim_without_one_is_found(self):
        self.assertEqual(claims('`vote.php` shows the ballot.'), ['`vote.php` shows'])
        self.assertEqual(claims('website/inc/kiosks.inc lists pages.'),
                         ['website/inc/kiosks.inc lists'])

    def test_a_plain_mention_passes(self):
        self.assertEqual(claims('See `setup.php` for the list.'), [])
        self.assertEqual(claims('The `setup.php` page and the port differ.'), [])
        self.assertEqual(claims('Mark the one from `config-database.inc` as active.'), [])

    def test_d98s_first_wording_fails_and_its_current_one_passes(self):
        self.assertEqual(claims(D98_FIRST), ['`fake-timer.php` links'])
        self.assertEqual(claims(D98_NOW), [])

    def test_a_possessive_and_an_adverb_before_the_verb(self):
        self.assertEqual(claims("`setup.php`'s status list shows it."),
                         ["`setup.php`'s status list shows"])
        self.assertEqual(claims('`setup.php` also links it.'), ['`setup.php` also links'])
        self.assertEqual(claims('`setup.php` quietly links it.'), ['`setup.php` quietly links'])
        self.assertEqual(claims("`setup.php`'s long status list shows it."),
                         ["`setup.php`'s long status list shows"])
        # Four words after the possessive are past the rule's three.
        self.assertEqual(claims("`setup.php`'s long status list here shows it."), [])
        self.assertEqual(claims('`setup.php`’s list shows it.'), ['`setup.php`’s list shows'])

    def test_each_adverb(self):
        adverbs = ('also', 'still', 'only', 'never', 'always', 'then', 'first')
        found = [claims(f'`a.php` {adverb} links b.') for adverb in adverbs]
        self.assertEqual(found, [[f'`a.php` {adverb} links'] for adverb in adverbs])

    def test_page_file_or_script_after_the_name(self):
        for noun in ('page', 'file', 'script'):
            with self.subTest(noun=noun):
                self.assertEqual(claims(f'The `a.php` {noun} shows y.'),
                                 [f'`a.php` {noun} shows'])
        self.assertEqual(claims('The `a.php` button shows y.'), [])

    def test_known_misses(self):
        """Forms the rule doesn't read, listed in #346's PR: an appositive,
        a Markdown link's name, a plural subject, a modal or a negated verb,
        and a cite of another file elsewhere in the sentence."""
        for text in ('`a.php`, the setup page, shows y.',
                     '[x](website/a.php) shows y.',
                     '`a.php` and `b.php` link x.',
                     '`a.php` will show x.',
                     "`a.php` doesn't link x.",
                     '`a.php` shows y (`b.php:9`).'):
            with self.subTest(text=text):
                self.assertEqual(claims(text), [])

    def test_the_cite_is_looked_for_in_the_claims_own_sentence(self):
        self.assertEqual(claims('`a.php` links b. See `a.php:3`.'), ['`a.php` links'])
        self.assertEqual(claims('See `a.php:3`! `a.php` links b.'), ['`a.php` links'])
        self.assertEqual(claims('Is it `a.php:3`? `a.php` links b.'), ['`a.php` links'])
        self.assertEqual(claims('`a.php` links b.\n`a.php:3`'), ['`a.php` links'])

    def test_each_line_and_its_number(self):
        found = php_cites.uncited('Text.\n\n`a.php` links b. `c.inc` sets d.')
        self.assertEqual(found, [(3, '`a.php` links', '`a.php` links b.'),
                                 (3, '`c.inc` sets', '`c.inc` sets d.')])

    def test_a_code_fence_and_a_url_are_left_out(self):
        self.assertEqual(claims('```\n`a.php` links b.\n```\nText.'), [])
        self.assertEqual(claims('  ```sh\n`a.php` links b.\n  ```'), [])
        self.assertEqual(claims('```\nx\n```\n`a.php` links b.'), ['`a.php` links'])
        self.assertEqual(claims('Open http://localhost/index.php returns 200.'), [])
        # No name starts after a `.` or a `-` either: neither `com/.a.php`
        # nor `y/a.php` is a name here.
        self.assertEqual(claims('Open https://x.com/.a.php links b.'), [])
        self.assertEqual(claims('Open https://x-y/a.php links b.'), [])

    def test_only_php_and_inc_files(self):
        self.assertEqual(claims('`kiosk.js` links b.'), [])
        self.assertEqual(claims('`templates/banner.inc` links b.'),
                         ['`templates/banner.inc` links'])

    def test_the_tickets_examples(self):
        found = [claims(f'`a.php` {verb} b.') for verb in TICKET_VERBS]
        self.assertEqual(found, [[f'`a.php` {verb}'] for verb in TICKET_VERBS])

    def test_each_verb(self):
        found = [claims(f'`a.php` {verb} b.') for verb in sorted(php_cites.VERBS)]
        self.assertEqual(found, [[f'`a.php` {verb}'] for verb in sorted(php_cites.VERBS)])
        self.assertEqual(claims('`a.php` frobs b.'), [])
        # A verb is a whole word: "set" is no claim inside "setting".
        self.assertEqual(claims('`a.php` setting b.'), [])


class Decisions(unittest.TestCase):
    def test_a_row_and_a_line_outside_a_row_are_keyed(self):
        text = f'Intro `a.php` links b.\n\n{D98_FIRST}\n{D98_NOW}\n'
        self.assertEqual(decision_claims(text),
                         {('line 1', '`a.php` links'), ('D98', '`fake-timer.php` links')})

    def test_an_exempt_claim_passes_and_a_stale_entry_is_named(self):
        exempt = {('D98', '`fake-timer.php` links'), ('D7', '`vote.php` shows')}
        self.assertEqual(check(D98_FIRST, exempt), ([], [('D7', '`vote.php` shows')]))
        self.assertEqual(check(D98_FIRST, set()), ([('D98', '`fake-timer.php` links')], []))
        self.assertEqual(check(D98_NOW, set()), ([], []))

    def test_every_decision_that_says_what_php_does_cites_the_line(self):
        uncited, _ = check(DECISIONS.read_text(encoding='utf-8'), EXEMPT)
        self.assertEqual(uncited, [],
                         'cite the PHP line (`fake-timer.php:99-100`) in the sentence')

    def test_each_exempt_row_is_still_uncited(self):
        """An exempt entry whose row is cited or gone is removed, so the list
        only shrinks."""
        _, stale = check(DECISIONS.read_text(encoding='utf-8'), EXEMPT)
        self.assertEqual(stale, [])


if __name__ == '__main__':
    unittest.main()
