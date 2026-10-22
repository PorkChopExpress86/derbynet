// A recording proxy between an unmodified shell test and the PHP server.
//
//   node recorder.mjs <listen-port> <upstream-port> <rawdir>
//
// A test script is given BASE_URL=http://localhost:<listen-port>/<tag>.  The
// proxy strips /<tag>, forwards the request to the server, relays the answer,
// and, while a recording is active for <tag>, writes down what the capture
// asks for.  Control requests start and end a recording:
//
//   GET /__control/begin?tag=T&record=a,b&steps=1   start recording tag T
//   GET /__control/end?tag=T                        write <rawdir>/T.http
//
//   record=  comma-separated action or query names to record in full ("*" for all)
//   steps=1  after each request that changes the roster, classes, ranks,
//            partitions, rounds or schedule, record a snapshot of that state
//
// Snapshots are taken with the proxy's own coordinator session, so they never
// disturb the test's login.  They are keyed by names (car number, class, rank,
// partition), never by row IDs, and each records only what changed since the
// one before.

import http from 'node:http';
import path from 'node:path';
import { writeFile } from './transcript.mjs';

const [listenPort, upstreamPort, rawdir] = [Number(process.argv[2]), Number(process.argv[3]), process.argv[4]];

const COORDINATOR = { name: 'RaceCoordinator', password: 'doyourbest' };

// Requests that never change the structure steps snapshots describe.
const NOT_STRUCTURAL = /^(timer-message|role\.login|replay[.-]|heat\.|result\.write|kiosk\.|scene\.|photo\.|award\.|message\.|vote\.|video\.|snapshot\.get|login$|roles$)/;

const recordings = new Map();   // tag -> { record, steps, exchanges, snap, stepNo }
let snapshotCookie = '';
let queue = Promise.resolve();

function upstream(method, pathAndQuery, { headers = {}, body = null } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: upstreamPort, method, path: pathAndQuery, headers },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
      });
    req.on('error', reject);
    if (body !== null) req.write(body);
    req.end();
  });
}

function absorbCookie(res) {
  for (const c of [].concat(res.headers['set-cookie'] ?? [])) {
    snapshotCookie = c.split(';')[0];
  }
}

async function snapshotGet(query) {
  const res = await upstream('GET', `/action.php?${query}`, { headers: { cookie: snapshotCookie } });
  absorbCookie(res);
  return res;
}

async function snapshotLogin() {
  snapshotCookie = '';
  const body = `action=role.login&name=${COORDINATOR.name}&password=${COORDINATOR.password}`;
  const res = await upstream('POST', '/action.php', {
    headers: { 'content-type': 'application/x-www-form-urlencoded', 'content-length': Buffer.byteLength(body), cookie: snapshotCookie },
    body,
  });
  absorbCookie(res);
}

async function snapshotJson(query) {
  let res = await snapshotGet(query);
  let json;
  try { json = JSON.parse(res.body.toString('utf8')); } catch { json = null; }
  if (json === null || (json.outcome && json.outcome.code === 'notauthorized')) {
    await snapshotLogin();
    res = await snapshotGet(query);
    json = JSON.parse(res.body.toString('utf8'));
  }
  return json;
}

// The state of the race as the roster, classes, ranks, partitions and rounds
// describe it.  Everything is keyed by name, so it survives a change in how
// row IDs are assigned.
async function readState() {
  const classes = await snapshotJson('query=class.list');
  const poll = await snapshotJson('query=poll&values=partitions,rounds');
  const racers = await snapshotJson('query=racer.list&order=car');
  const classNames = new Map();
  const rankNames = new Map();
  for (const c of classes.classes ?? []) {
    classNames.set(c.classid, c.name);
    for (const r of c.subgroups ?? []) rankNames.set(r.rankid, r.name);
  }
  return {
    classes: (classes.classes ?? []).map((c) => ({
      name: c.name, count: c.count, nrounds: c.nrounds, ntrophies: c.ntrophies,
      subgroups: (c.subgroups ?? []).map((r) => ({ name: r.name, count: r.count, ntrophies: r.ntrophies })),
    })),
    partitions: (poll.partitions ?? []).map((p) => ({ name: p.name, count: p.count })),
    rounds: (poll.rounds ?? []).map((r) => ({
      class: r.class, round: r.round, name: r.name, aggregate: r.aggregate, roster_size: r.roster_size,
      passed: r.passed, unscheduled: r.unscheduled, heats_scheduled: r.heats_scheduled, heats_run: r.heats_run,
    })),
    roster: (racers.racers ?? []).map((r) => JSON.stringify({
      carnumber: r.carnumber, name: `${r.firstname} ${r.lastname}`, carname: r.carname,
      partition: r.partition, class: classNames.get(r.classid) ?? r.class, rank: rankNames.get(r.rankid) ?? r.rank,
      passed: r.passed,
    })),
  };
}

function diffState(prev, next) {
  const change = {};
  for (const k of ['classes', 'partitions', 'rounds']) {
    if (!prev || JSON.stringify(prev[k]) !== JSON.stringify(next[k])) change[k] = next[k];
  }
  const count = (rows) => rows.reduce((m, r) => m.set(r, (m.get(r) ?? 0) + 1), new Map());
  const before = count(prev ? prev.roster : []);
  const after = count(next.roster);
  const added = [];
  const removed = [];
  for (const [row, n] of after) for (let i = before.get(row) ?? 0; i < n; ++i) added.push(JSON.parse(row));
  for (const [row, n] of before) for (let i = after.get(row) ?? 0; i < n; ++i) removed.push(JSON.parse(row));
  if (added.length || removed.length) change.roster = { added, removed };
  return change;
}

function nameOf(method, rest) {
  const u = new URL(rest, 'http://x');
  return method === 'POST' ? null : u.searchParams.get('query');
}

function actionOf(method, rest, bodyText) {
  if (method === 'POST') {
    const m = /(?:^|&)action=([^&]*)/.exec(bodyText);
    return m ? decodeURIComponent(m[1].replace(/\+/g, ' ')) : null;
  }
  return nameOf(method, rest);
}

async function handle(req, res) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks);
  const url = new URL(req.url, 'http://x');

  if (url.pathname.startsWith('/__control/')) {
    const tag = url.searchParams.get('tag');
    if (url.pathname === '/__control/begin') {
      const rec = {
        record: (url.searchParams.get('record') ?? '').split(',').filter(Boolean),
        steps: url.searchParams.get('steps') === '1',
        exchanges: [], snap: null, stepNo: 0, seq: 0,
      };
      recordings.set(tag, rec);
      if (rec.steps) {
        rec.snap = await readState();
        rec.exchanges.push({
          name: 'step-0 initial state', request: 'SNAPSHOT', status: 200, contentType: 'application/json',
          body: JSON.stringify({ step: 0, state: rec.snap }) + '\n',
        });
      }
      res.end('ok\n');
    } else if (url.pathname === '/__control/end') {
      const rec = recordings.get(tag);
      recordings.delete(tag);
      if (rec) writeFile(path.join(rawdir, `${tag.replace(/__/g, "/")}.http`), rec.exchanges);
      res.end(`${rec ? rec.exchanges.length : 0}\n`);
    } else {
      res.statusCode = 404;
      res.end();
    }
    return;
  }

  const m = /^\/([^/]+)(\/.*)?$/.exec(url.pathname);
  const tag = m ? m[1] : null;
  const rec = tag ? recordings.get(tag) : undefined;
  const rest = (m && m[2] ? m[2] : '/') + url.search;
  const headers = { ...req.headers };
  delete headers.host;
  headers.connection = 'close';
  const started = performance.now();
  const up = await upstream(req.method, rest, { headers, body: body.length ? body : null });
  const elapsedMs = Math.round(performance.now() - started);

  const relay = { ...up.headers };
  delete relay['transfer-encoding'];
  delete relay.connection;
  relay['content-length'] = up.body.length;
  res.writeHead(up.status, relay);
  res.end(up.body);

  if (!rec) return;
  const bodyText = body.toString('utf8');
  const name = actionOf(req.method, rest, bodyText);
  if (rec.record.includes('*') || (name && rec.record.includes(name))) {
    const ex = {
      name: `${String(++rec.seq).padStart(3, '0')} ${name ?? rest}`,
      request: `${req.method} ${rest.replace(/^\//, '')}`,
      status: up.status, contentType: up.headers['content-type'], location: up.headers.location,
      setCookies: [].concat(up.headers['set-cookie'] ?? []), cacheControl: up.headers['cache-control'],
      body: up.body, elapsedMs,
    };
    if (req.method === 'POST') ex.requestBody = bodyText;
    rec.exchanges.push(ex);
  }
  if (rec.steps && req.method === 'POST' && name && !NOT_STRUCTURAL.test(name)) {
    const next = await readState();
    const change = diffState(rec.snap, next);
    rec.snap = next;
    rec.exchanges.push({
      name: `step-${++rec.stepNo} after ${name}`,
      request: `SNAPSHOT after ${req.method} ${rest.replace(/^\//, '')}`,
      requestBody: bodyText.replace(/(^|&)password=[^&]*/g, '$1password=...'),
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ step: rec.stepNo, changed: change }) + '\n',
    });
  }
}

http.createServer((req, res) => {
  queue = queue.then(() => handle(req, res)).catch((e) => {
    console.error('recorder:', e);
    if (!res.headersSent) res.statusCode = 502;
    res.end();
  });
}).listen(listenPort, '127.0.0.1', () => console.log(`recorder listening on ${listenPort}`));
