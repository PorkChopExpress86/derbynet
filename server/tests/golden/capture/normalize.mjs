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

// Rules that look at a whole exchange, not just its body, and can add notes.
export const EXCHANGE_RULES = [
  {
    id: 'reschedule-timing',
    doc: 'The seconds schedule.reschedule reports in "timing" and in the adjuster trace.  A search that hit its wall-clock deadline is not deterministic, so its exchange is left out and listed in a .omitted.txt beside the transcript; every other reschedule gets a "deterministic" note.',
    apply: (ex) => {
      if (!/(^|&)action=schedule\.reschedule(&|$)/.test(ex.requestBody ?? '') || !isJson(ex)) return ex;
      let text = ex.body.toString('utf8');
      if (/"run-\d+-timed-out"/.test(text)) {
        return { omit: `${ex.name} (${ex.requestBody}): the search ran into its deadline, so its result is not deterministic` };
      }
      const mask = (block) => block.replace(/: \d+/g, ': "<seconds>"');
      text = text
        .replace(/("timing": \{)([\s\S]*?)(\n    \})/, (m, a, b, c) => a + mask(b) + c)
        .replace(/("times": \{)([\s\S]*?)(\n {8}\})/, (m, a, b, c) => a + mask(b) + c);
      // With trace=1 the response carries the adjuster's whole search trace, up
      // to 900 KB.  Keep its SHA-256 and size, and everything else in full.
      text = text.replace(/\n    "adjuster": \{[\s\S]*?\n    \}(,?)/, (m, comma) =>
        `\n    "adjuster": "<sha256 ${crypto.createHash('sha256').update(m).digest('hex')} of ${Buffer.byteLength(m)} bytes>"${comma}`);
      return { ...ex, body: Buffer.from(text, 'utf8'), notes: [...(ex.notes ?? []), 'deterministic: the search ended before its deadline'] };
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
  return ex;
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
      const all = parse(fs.readFileSync(file)).map(normalizeExchange);
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
