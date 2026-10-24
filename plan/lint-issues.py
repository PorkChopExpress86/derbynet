"""Lint the Django migration plan's issue bodies, read live from GitHub.

Usage: python3 -I plan/lint-issues.py [--repo OWNER/NAME] [--file N=PATH ...]

Reads every issue labeled django-migration, then checks each body for:
- style: no em or en dashes, curly quotes, mid-sentence colons, or filler words
- section order for kind:spec and kind:ticket issues
- cited repository paths that exist in this checkout
- #N references that name an existing issue
- forward-only ## Dependencies, with no cycles across the plan

--file N=PATH lints a local draft in place of issue N's live body, so an edit can be
checked before `gh issue edit N --body-file PATH`. Exits 1 on any finding.
"""
import json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH = os.environ.get('GH', 'gh')
args = sys.argv[1:]
repo = 'PorkChopExpress86/derbynet'
drafts = {}
while args:
    a = args.pop(0)
    if a == '--repo':
        repo = args.pop(0)
    elif a == '--file':
        n, path = args.pop(0).split('=', 1)
        drafts[int(n)] = open(path, encoding='utf-8').read()
    else:
        sys.exit(__doc__)

out = subprocess.run([GH, 'issue', 'list', '--repo', repo, '--label', 'django-migration', '--state', 'all',
                      '--limit', '500', '--json', 'number,state,body,labels'],
                     capture_output=True, text=True, check=True).stdout
issues = {i['number']: i for i in json.loads(out)}
for n, body in drafts.items():
    issues[n]['body'] = body


def numbers(kind):
    out = subprocess.run([GH, kind, 'list', '--repo', repo, '--state', 'all', '--limit', '1000', '--json', 'number'],
                         capture_output=True, text=True, check=True).stdout
    return {i['number'] for i in json.loads(out)}


all_numbers = numbers('issue')
prs = numbers('pr')

TICKET = ['Goal', 'Current PHP behavior', 'Scope', 'Out of scope', 'Acceptance criteria',
          'Risks and notes', 'Dependencies', 'References']
SPEC = ['Summary', 'Context', 'Proposal', 'Alternatives considered', 'Open questions for the owner',
        'Acceptance criteria', 'Dependencies', 'References']
# Sections a body may add without breaking the order check.
EXTRA = {'PHP defects to fix', 'Owner decisions'}
# server/ is left out: the plan cites many server/ files that M1 to M6 create.
REPO_ROOTS = ('website/', 'testing/', 'timer/', 'extras/', 'installer/', 'docs/', 'templates/', '.github/', 'plan/')
WEBSITE_ROOTS = ('js/', 'css/', 'inc/', 'kiosks/', 'img/', 'ajax/', 'print/', 'sql/')
# Cited paths that do not exist on purpose: build outputs (gitignored) and files a ticket creates.
ALLOWED_MISSING = {
    'website/inc/banner.inc': 'Ant output, gitignored copy of templates/banner.inc',
    'website/timer.php': 'Ant output, gitignored',
    'website/local': 'runtime config folder, gitignored',
    'website/js/timer/': 'Ant output, gitignored',
    'website/js/timer/profiles.js': 'Ant output, gitignored',
    'js/timer/clock-worker.js': 'Ant output, gitignored',
    'website/js/adapter.js': 'fetched by the webrtc-adapter Ant target, gitignored',
    'js/adapter.js': 'fetched by the webrtc-adapter Ant target, gitignored',
    'js/please-check-in.js': 'referenced by please-check-in.kiosk but missing from the repo (a PHP defect)',
    'testing/output.curl': 'suite run artifact, gitignored',
    'testing/debug.curl': 'suite run artifact, gitignored',
    'docs/dist/Quick Reference.pdf': 'Ant output',
    'docs/dist/': 'created by docs',
    'website/quickref.pdf': 'Ant output',
    '.github/workflows/server.yml': 'created by scaffold',
    'installer/debian/server-django/': 'created by pkg-debian',
    'installer/desktop/': 'created by pkg-desktop',
    'docs/tools/fodt-to-md.sh': 'created by docs',
    'docs/developer-guide.md': 'created by docs',
    'docs/upgrading-from-php.md': 'created by cutover',
    'sql/odbc/schema.inc': 'named as missing by php-defects C39',
    'testing/query.sh': 'named as missing by php-defects C45',
}
REVERSE = re.compile(r'depends? on this|depend on this|^- (Related to|Works best|Feeds|Uses|Consumed by)'
                     r'|run-time needs|not build blockers', re.I)
FOOTER = re.compile(r'\n---\n_Part of the Django migration plan\..*\Z', re.S)

findings, notes, deps = [], [], {}


def strip_code(line):
    line = re.sub(r'`[^`]*`', '``', line)
    line = re.sub(r'"[^"]*"', '""', line)  # quoted UI text keeps its own punctuation
    return re.sub(r'https?://\S+', 'URL', line)


def section(text, name):
    m = re.search(r'^## ' + re.escape(name) + r'\n(.*?)(?=^## |\Z)', text, re.S | re.M)
    return m.group(1) if m else None


def check_path(n, tok):
    p = re.sub(r':\d+(-\d+)?$', '', tok.strip())
    if any(c in p for c in '<>*{}?=&%$'):
        return
    if p.startswith(REPO_ROOTS) or p in ('build.xml', 'README.md'):
        cands = [p]
    elif p.startswith(WEBSITE_ROOTS):
        cands = [p, 'website/' + p]
    else:
        return
    if any(os.path.exists(os.path.join(ROOT, c.rstrip('/'))) for c in cands):
        return
    if ' ' in p and os.path.exists(os.path.join(ROOT, p.split(' ')[0])):
        return
    if p in ALLOWED_MISSING:
        notes.append(p)
        return
    findings.append(f'#{n}: missing path `{p}`')


def kind(issue):
    names = {l['name'] for l in issue['labels']}
    return 'spec' if 'kind:spec' in names else 'ticket' if 'kind:ticket' in names else 'epic'


for n, issue in sorted(issues.items()):
    text = FOOTER.sub('', issue['body'].replace('\r\n', '\n'))
    k = kind(issue)
    if k == 'epic' and n != 2:
        continue  # milestone epics are generated checklists; the roadmap (#2) is prose
    heads = [h for h in re.findall(r'^## (.+)$', text, re.M) if h not in EXTRA]
    if k in ('spec', 'ticket') and issue['state'] == 'OPEN':
        want = SPEC if k == 'spec' else TICKET
        if heads != want:
            findings.append(f'#{n}: sections {heads} != {want}')
    in_fence = False
    for ln, line in enumerate(text.splitlines(), 1):
        if line.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for ch, name in (('—', 'em dash'), ('–', 'en dash'), ('“', 'curly quote'),
                         ('”', 'curly quote'), ('‘', 'curly apostrophe'), ('’', 'curly apostrophe')):
            if ch in line:
                findings.append(f'#{n}:{ln}: {name}')
        bare = strip_code(line)
        if line.startswith('#') or re.match(r'^\|[-| :]+\|$', bare):
            continue
        for m in re.finditer(r'(\S):(\s+)(\S)', bare):
            findings.append(f'#{n}:{ln}: mid-sentence colon: ...{bare[max(0, m.start() - 30):m.end() + 20]}')
        for m in re.finditer(r'\b(e\.g\.|i\.e\.|etc\.|simply|easily|leverage|utilize|robust|seamless|Additionally)',
                             bare, re.I):
            findings.append(f'#{n}:{ln}: word {m.group(1)}')
        for ref in re.findall(r'(?<![\w/&])#(\d+)\b', bare):
            if int(ref) not in all_numbers and int(ref) not in prs:
                findings.append(f'#{n}:{ln}: reference #{ref} names no issue or PR')
    for tok in set(re.findall(r'`([^`]+)`', text)):
        check_path(n, tok)
    dep_text = section(text, 'Dependencies')
    if dep_text is not None:
        for line in dep_text.splitlines():
            if '#' in line and REVERSE.search(line):
                findings.append(f'#{n}: Dependencies line is not a forward dependency: {line.strip()[:100]}')
        found = sorted({int(d) for d in re.findall(r'(?<![\w/&])#(\d+)\b', strip_code(dep_text))} & issues.keys())
        if n in found:
            findings.append(f'#{n}: depends on itself')
        deps[n] = [d for d in found if d != n and kind(issues[d]) != 'epic']

state, stack = {}, []


def visit(s):
    state[s] = 1
    stack.append(s)
    for d in deps.get(s, []):
        if state.get(d) == 1:
            findings.append('dependency cycle: ' + ' -> '.join(f'#{x}' for x in stack[stack.index(d):] + [d]))
        elif d not in state:
            visit(d)
    stack.pop()
    state[s] = 2


for s in sorted(deps):
    if s not in state:
        visit(s)

for f in findings:
    print(f)
print(f'{len(findings)} findings, {len(set(notes))} allowed missing paths, {len(issues)} issues, '
      f'{sum(len(v) for v in deps.values())} dependency edges')
sys.exit(1 if findings else 0)
