"""A sentence that says what a PHP file does cites the line that does it (#346).

D98 first said PHP's `fake-timer.php` links the dashboard and the timer test,
where `fake-timer.php:99-100` links `coordinator.php` and `timer.php`; #307's
ticket carried three cites one line off and one wrong target. A sentence that
names a PHP file (`*.php` or `*.inc`, under `website/` or the `templates/`
build.xml copies into it) with a verb right after the name, or after the
name's possessive and up to three words ("`setup.php`'s status list shows"),
needs a `file:line` cite in that sentence. A name with no such verb after it
is a plain mention, and passes. Whether the cited line says what the sentence
claims still takes a read.

`plan/lint-issues.py` applies this to open issue bodies, and
`plan/tests/test_php_cites.py` to `plan/owner-decisions.md`. The cite pattern
is `server/tests/test_php_cite_lines.py`'s `CITE`, loaded from that file, so
the two read one form of cite.
"""
import importlib.util
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cite_pattern():
    path = os.path.join(ROOT, 'server', 'tests', 'test_php_cite_lines.py')
    spec = importlib.util.spec_from_file_location('_php_cite_lines', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CITE


CITE = _cite_pattern()
# The ticket's examples (links, shows, stores, writes, answers, sets, refuses,
# calls), every verb that followed a PHP file's name in an uncited sentence of
# an open issue on 2026-11-16 (a snapshot, so a later issue's new verb is not
# read until it is added here), and their past forms.
VERBS = frozenset('''
    links shows stores writes answers sets refuses calls returns sends reads
    draws takes keeps uses checks polls lists prints redirects renders accepts
    rejects puts adds deletes removes saves loads counts sorts gives names opens
    starts runs waits hides displays records fetches updates inserts does echoes
    serves defines parses requires maps posts needs has tests wraps holds
    excludes preserves depends handles pauses leaves
    linked showed shown stored wrote answered refused called returned sent drew
    took kept used checked polled listed printed redirected rendered accepted
    rejected added deleted removed saved loaded counted sorted gave named opened
    started ran waited hid displayed recorded fetched updated inserted did
    echoed served defined parsed required mapped posted needed had tested
    wrapped held excluded preserved depended handled paused left
    set read put
'''.split())
# The look-behind starts no name inside a URL or a longer path.
FILE = r"(?<![\w/.-])(?:[\w-]+/)*[\w.-]+\.(?:php|inc)\b"
CLAIM = re.compile(
    r"`?(?P<file>" + FILE + r")`?"
    r"(?:'s(?:\s+[\w`()-]+){0,3}?)?"
    r"\s+(?:(?:also|still|only|never|always|then|first|\w+ly)\s+)?"
    r"(?P<verb>" + "|".join(sorted(VERBS)) + r")\b"
)
SENTENCE_END = re.compile(r'(?<=[.!?])\s+')


def claim(sentence):
    """The words of the sentence's first claim about what a PHP file does,
    or None."""
    match = CLAIM.search(sentence)
    return match.group() if match else None


def uncited(text):
    """`(line, claim, sentence)` for each sentence outside a code fence that
    says what a PHP file does and holds no `file:line` cite. A sentence is
    read within its line, split after `.`, `!` or `?`."""
    found, in_fence = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for sentence in SENTENCE_END.split(line):
            words = claim(sentence)
            if words and not CITE.search(sentence):
                found.append((number, words, sentence.strip()))
    return found
