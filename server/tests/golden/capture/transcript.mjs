// The transcript format shared by capture-http.sh, recorder.mjs and normalize.mjs.
//
// A transcript file is a sequence of exchanges.  Each exchange is
//
//   ### <name>
//   request: <METHOD> <path and query>
//   user-agent: <value>            (only when the client sent a chosen one)
//   request-body: <exact body>     (POST only, one line)
//   status: <HTTP status>
//   content-type: <value>          (when present)
//   location: <value>              (when present)
//   note: <free text>              (zero or more)
//   elapsed-ms: <milliseconds>     (raw transcripts only; normalize.mjs reads and drops it)
//   length: <number of body bytes>
//   body:
//   <exactly that many bytes>
//   <one newline>
//
// The length line is what makes the body unambiguous, whatever it contains.

import fs from 'node:fs';
import path from 'node:path';

export function serialize(ex) {
  const body = Buffer.isBuffer(ex.body) ? ex.body : Buffer.from(ex.body ?? '', 'utf8');
  const head = [`### ${ex.name}`, `request: ${ex.request}`];
  if (ex.userAgent) head.push(`user-agent: ${ex.userAgent}`);
  if (ex.requestBody !== undefined && ex.requestBody !== null) head.push(`request-body: ${ex.requestBody}`);
  head.push(`status: ${ex.status}`);
  if (ex.contentType) head.push(`content-type: ${ex.contentType}`);
  if (ex.location) head.push(`location: ${ex.location}`);
  for (const n of ex.notes ?? []) head.push(`note: ${n}`);
  if (ex.elapsedMs !== undefined) head.push(`elapsed-ms: ${ex.elapsedMs}`);
  head.push(`length: ${body.length}`, 'body:');
  return Buffer.concat([Buffer.from(head.join('\n') + '\n', 'utf8'), body, Buffer.from('\n')]);
}

export function parse(buf) {
  const exchanges = [];
  let pos = 0;
  while (pos < buf.length) {
    const ex = { notes: [] };
    for (;;) {
      const eol = buf.indexOf(0x0a, pos);
      if (eol < 0) throw new Error('truncated transcript header');
      const line = buf.toString('utf8', pos, eol);
      pos = eol + 1;
      if (line === 'body:') break;
      const m = /^(###|[a-z-]+:) ?(.*)$/.exec(line);
      if (!m) throw new Error(`bad transcript line: ${line}`);
      const [, key, value] = m;
      switch (key) {
        case '###': ex.name = value; break;
        case 'request:': ex.request = value; break;
        case 'user-agent:': ex.userAgent = value; break;
        case 'request-body:': ex.requestBody = value; break;
        case 'status:': ex.status = Number(value); break;
        case 'content-type:': ex.contentType = value; break;
        case 'location:': ex.location = value; break;
        case 'note:': ex.notes.push(value); break;
        case 'elapsed-ms:': ex.elapsedMs = Number(value); break;
        case 'length:': ex.length = Number(value); break;
        default: throw new Error(`unknown transcript key ${key}`);
      }
    }
    ex.body = buf.subarray(pos, pos + ex.length);
    pos += ex.length;
    if (buf[pos] !== 0x0a) throw new Error(`transcript body of ${ex.name} not terminated`);
    pos += 1;
    delete ex.length;
    exchanges.push(ex);
  }
  return exchanges;
}

export function readFile(file) {
  return parse(fs.readFileSync(file));
}

export function writeFile(file, exchanges) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, Buffer.concat(exchanges.map(serialize)));
}
