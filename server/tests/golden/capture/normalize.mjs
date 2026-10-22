// The one place where volatile values are masked before a fixture is written.
//
//   node normalize.mjs <rawdir> <outdir>          normalize every raw transcript
//   node normalize.mjs --list-rules               print the rule ids
//   node normalize.mjs --check-readme <README.md> fail unless the README names every rule
//
// A rule rewrites the text of one response body, or marks a response, and it
// changes only the volatile token, so the rest of the body stays byte for byte
// what the server sent.  Every rule has a stable id, and README.md documents
// each id; --check-readme enforces that.

import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import { parse, writeFile } from './transcript.mjs';

const isJson = (ex) => /json/.test(ex.contentType ?? '');
const isXml = (ex) => /xml/.test(ex.contentType ?? '');
const isText = (ex) => /json|xml|html|text/.test(ex.contentType ?? '');

// Each rule is { id, doc, apply(ex, text) -> text }.  `text` is the body as a string.
export const RULES = [
  {
    id: 'timecheck',
    doc: 'The server clock the role.login JSON response carries as "timecheck".',
    apply: (ex, text) => isJson(ex)
      ? text.replace(/("timecheck": )"[^"]*"/g, '$1"<timecheck>"')
      : text,
  },
  {
    id: 'last-contact',
    doc: 'Epoch seconds of the last contact from a timer, replay or kiosk ("last-contact", "last_contact"), when not zero.',
    apply: (ex, text) => isJson(ex)
      ? text.replace(/("last[-_]contact": )[1-9]\d*/g, '$1"<epoch>"')
      : text,
  },
  {
    id: 'timer-contact-age',
    doc: 'The seconds since the last contact in a state message such as "CONNECTED (5 sec.)" or "Ready (5 sec.)", which become "(<n> sec.)", and the "age" of a kiosk.',
    apply: (ex, text) => isJson(ex)
      ? text.replace(/\(\d+ sec\.\)/g, '(<n> sec.)').replace(/("age": )\d+/g, '$1"<seconds>"')
      : text,
  },
  {
    id: 'completed-timestamp',
    doc: 'A "YYYY-MM-DD HH:MM:SS" string in a JSON body, which is when a heat result was written (RaceChart.completed), becomes "<timestamp>".',
    apply: (ex, text) => isJson(ex)
      ? text.replace(/"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"/g, '"<timestamp>"')
      : text,
  },
  {
    id: 'export-results-order',
    doc: 'The Results sheet of export-json.php lists heats ORDER BY completed, heat, lane, and completed has one-second resolution, so rows that finished in different seconds from run to run swap places.  The rows are sorted by class, round, heat, lane instead.',
    apply: (ex, text) => {
      if (!isJson(ex)) return text;
      return text.replace(/("Results",\n {8}\[\n)([\s\S]*?)(\n {8}\]\n {4}\])/, (m, head, rows, tail) => {
        const [header, ...data] = rows.split(/(?<=\n {12}\]),\n(?= {12}\[\n)/);
        const key = (r) => JSON.parse(r);
        data.sort((a, b) => {
          const [x, y] = [key(a), key(b)];
          for (const i of [0, 1, 2, 3]) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
          return 0;
        });
        return head + [header, ...data].join(',\n') + tail;
      });
    },
  },
  {
    id: 'award-duration',
    doc: 'The "duration" query=award.current reports, a measured time in seconds.',
    apply: (ex, text) => isJson(ex)
      ? text.replace(/("duration": )[0-9.eE+-]+/g, '$1"<seconds>"')
      : text,
  },
];

// A schedule.reschedule search runs once per run per lane, each with a deadline
// of intdiv(INJECTION_TIME_LIMIT, n_runs_per_lane) seconds, and stops at it only
// once time() > deadline, which is more than that many seconds after it began
// (website/ajax/action.schedule.reschedule.inc).  The response cannot say whether
// that happened: "run-N-timed-out" is also set when a search stops on a perfect
// score, and the seconds it reports are whole.  The request's wall time bounds
// every search in it, so an exchange is kept only when its wall time is under
// 1/DEADLINE_MARGIN of the per-run deadline: a machine DEADLINE_MARGIN times
// slower still ends every search before its deadline.  An omitted reschedule
// that was not a dry run wrote the schedule every later one starts from, so
// main() omits the rest of that transcript too.
const INJECTION_TIME_LIMIT = 10;
const DEADLINE_MARGIN = 2;

function rescheduleRunLimit(json) {
  const runs = Object.keys(json.timing?.internal ?? {})
    .map((k) => /^run-(\d+)-/.exec(k)).filter(Boolean).map((m) => Number(m[1]));
  const nRuns = json.adjuster?.n_runs_per_lane ?? (runs.length ? Math.max(...runs) + 1 : null);
  return nRuns === null ? null : Math.trunc(INJECTION_TIME_LIMIT / nRuns);
}

// Rules that look at a whole exchange, not just its body, and can add notes.
export const EXCHANGE_RULES = [
  {
    id: 'reschedule-timing',
    doc: 'The seconds schedule.reschedule reports in "timing" and in the adjuster trace.  A reschedule whose request took half its search deadline or longer could be cut off by the deadline on a slower machine, so its exchange, and every later exchange in the transcript if it changed the schedule, is left out and listed in a .omitted.txt beside the transcript; every other reschedule gets a "deterministic" note.',
    apply: (ex) => {
      if (!/(^|&)action=schedule\.reschedule(&|$)/.test(ex.requestBody ?? '') || !isJson(ex)) return ex;
      let text = ex.body.toString('utf8');
      if (ex.elapsedMs === undefined) throw new Error(`${ex.name}: a schedule.reschedule exchange has no elapsed-ms`);
      const json = JSON.parse(text);
      const limit = rescheduleRunLimit(json);
      if (limit === null) throw new Error(`${ex.name}: cannot tell the search deadline from the response`);
      const flagged = Object.keys(json.timing?.internal ?? {}).filter((k) => /^run-\d+-timed-out$/.test(k));
      const kept = ex.elapsedMs * DEADLINE_MARGIN < limit * 1000;
      console.error(`reschedule ${ex.name}: ${ex.elapsedMs} ms, ${limit} s deadline per run, ` +
                    `${flagged.length ? flagged.join(' ') : 'no timed-out flag'}: ${kept ? 'kept' : 'omitted'}`);
      if (!kept) {
        return {
          omit: `${ex.name} (${ex.requestBody}): the request took ${limit / DEADLINE_MARGIN} s or longer, 1/${DEADLINE_MARGIN} of its ${limit} s search deadline, so a slower machine could cut the search off`,
          changedState: !/(^|&)dry-run=1(&|$)/.test(ex.requestBody),
        };
      }
      const mask = (block) => block.replace(/: \d+/g, ': "<seconds>"');
      text = text
        .replace(/("timing": \{)([\s\S]*?)(\n    \})/, (m, a, b, c) => a + mask(b) + c)
        .replace(/("times": \{)([\s\S]*?)(\n {8}\})/, (m, a, b, c) => a + mask(b) + c);
      // With trace=1 the response carries the adjuster's whole search trace, up
      // to 900 KB.  Keep its SHA-256 and size, and everything else in full.
      text = text.replace(/\n    "adjuster": \{[\s\S]*?\n    \}(,?)/, (m, comma) =>
        `\n    "adjuster": "<sha256 ${crypto.createHash('sha256').update(m).digest('hex')} of ${Buffer.byteLength(m)} bytes>"${comma}`);
      return { ...ex, body: Buffer.from(text, 'utf8'), notes: [...(ex.notes ?? []), `deterministic: the request took under 1/${DEADLINE_MARGIN} of its ${limit} s search deadline`] };
    },
  },
];

export function normalizeExchange(ex) {
  let text = isText(ex) ? ex.body.toString('utf8') : null;
  if (text !== null) {
    for (const rule of RULES) text = rule.apply(ex, text);
    ex = { ...ex, body: Buffer.from(text, 'utf8') };
  }
  for (const rule of EXCHANGE_RULES) {
    ex = rule.apply(ex);
    if (ex.omit) return ex;
  }
  const { elapsedMs, ...kept } = ex;
  return kept;
}

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name < b.name ? -1 : 1)) {
    if (entry.name.startsWith('.')) continue;
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(p);
    else yield p;
  }
}

function main(argv) {
  if (argv[0] === '--list-rules') {
    for (const r of [...RULES, ...EXCHANGE_RULES]) console.log(r.id);
    return;
  }
  if (argv[0] === '--check-readme') {
    const readme = fs.readFileSync(argv[1], 'utf8');
    const missing = [...RULES, ...EXCHANGE_RULES].filter((r) => !readme.includes('`' + r.id + '`'));
    if (missing.length) {
      console.error(`README does not document normalization rule(s): ${missing.map((r) => r.id).join(', ')}`);
      process.exit(1);
    }
    return;
  }
  const [rawdir, outdir] = argv;
  let files = 0;
  for (const file of walk(rawdir)) {
    const rel = path.relative(rawdir, file);
    if (file.endsWith('.http')) {
      let changedBy = null;
      const all = parse(fs.readFileSync(file)).map((ex) => {
        if (changedBy) return { omit: `${ex.name} (${ex.requestBody ?? ex.request}): starts from the schedule that ${changedBy} wrote` };
        const out = normalizeExchange(ex);
        if (out.changedState) changedBy = ex.name;
        return out;
      });
      const kept = all.filter((e) => !e.omit);
      writeFile(path.join(outdir, rel), kept);
      if (kept.length < all.length) {
        const omitted = all.filter((e) => e.omit).map((e) => `${e.omit}\n`).join('');
        fs.writeFileSync(path.join(outdir, rel.replace(/\.http$/, '.omitted.txt')), omitted);
      }
    } else {
      fs.mkdirSync(path.dirname(path.join(outdir, rel)), { recursive: true });
      fs.copyFileSync(file, path.join(outdir, rel));
    }
    ++files;
  }
  console.error(`normalize: ${files} files`);
}

if (import.meta.url === `file://${process.argv[1]}`) main(process.argv.slice(2));
