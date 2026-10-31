// SPIKE #71: drives the server through the legacy API (action.php), as the
// race coordinator and the timer would.  Used by the Playwright scripts and on
// the command line:
//   node tools/driver.js heat [base] [heatno]   race one Lions & Tigers heat
//   node tools/driver.js reveal [base] N|all    step the standings reveal
//   node tools/driver.js page [base] now-racing|standings  point every kiosk
'use strict';

class Session {
  constructor(base) { this.base = base.replace(/\/$/, ''); this.cookie = ''; }
  async req(method, params) {
    const body = new URLSearchParams(params).toString();
    const url = this.base + '/action.php' + (method === 'GET' ? '?' + body : '');
    const res = await fetch(url, {
      method,
      headers: { cookie: this.cookie,
                 'content-type': 'application/x-www-form-urlencoded' },
      body: method === 'POST' ? body : undefined,
    });
    const set = res.headers.get('set-cookie');
    if (set) {
      const jar = Object.fromEntries(this.cookie.split('; ').filter(Boolean).map(c => c.split('=')));
      for (const part of set.split(/,(?=\s*\w+=)/)) {
        const [kv] = part.trim().split(';');
        const [k, v] = kv.split('=');
        jar[k] = v;
      }
      this.cookie = Object.entries(jar).map(([k, v]) => k + '=' + v).join('; ');
    }
    const text = await res.text();
    try { return JSON.parse(text); } catch { return text; }
  }
  get(params) { return this.req('GET', params); }
  post(params) { return this.req('POST', params); }
  async login(name, password) {
    return this.post({ action: 'role.login', name, password });
  }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function coordinator(base) {
  const s = new Session(base);
  await s.login('RaceCoordinator', 'doyourbest');
  return s;
}

async function roundIdOf(s, prefix) {
  const r = await s.get({ query: 'poll', values: 'rounds' });
  return r.rounds.find(x => String(x.class).startsWith(prefix) && x.round === 1).roundid;
}

// Stages heat `heat` of Lions & Tigers round 1 afresh (its old results are
// deleted), waits `stageMs` so the kiosks show the new lineup, then the timer
// reports STARTED and, `runMs` later, FINISHED with random times.  Returns the
// wall-clock ms at which FINISHED was acknowledged.
async function raceHeat(base, heat, { stageMs = 3000, runMs = 2500, s } = {}) {
  s = s || await coordinator(base);
  const roundid = await roundIdOf(s, 'Lions');
  await s.post({ action: 'result.delete', roundid, heat });
  await s.post({ action: 'heat.select', roundid, heat, now_racing: 1 });
  const timer = new Session(base);
  await timer.login('Timer', '');
  await timer.post({ action: 'timer-message', message: 'HELLO' });
  await timer.post({ action: 'timer-message', message: 'IDENTIFIED', nlanes: 4 });
  // heat.select may have moved on if the timer was not connected; reselect.
  await s.post({ action: 'heat.select', roundid, heat, now_racing: 1 });
  await sleep(stageMs);
  await timer.post({ action: 'timer-message', message: 'STARTED' });
  await sleep(runMs);
  const lanes = {};
  for (let lane = 1; lane <= 4; lane++) lanes['lane' + lane] = (2 + Math.random() * 2).toFixed(4);
  await timer.post({ action: 'timer-message', message: 'FINISHED', ...lanes });
  const finishedAt = Date.now();
  // timer-message has moved the current heat on; the kiosks show these
  // results, linger, then slide in the next lineup.
  return { roundid, heat, finishedAt };
}

// Every kiosk gets one page and parameter string in a single write
// (`kiosk.assign&all=`), so all kiosks see the change at their next poll.
async function assignAll(base, page, params, s) {
  s = s || await coordinator(base);
  await s.post({ action: 'kiosk.assign', all: page + '#' + JSON.stringify(params) });
  return Date.now();
}

// After the fixture: Lions & Tigers' heats are unraced again and the master
// schedule is off, so each raced heat is followed by an unraced lineup.
// White's Wolves (13 racers, every heat run) is the class the reveal steps.
async function resetLions(base) {
  const s = await coordinator(base);
  await s.post({ action: 'settings.write', 'use-master-sched-checkbox': 1, 'use-master-sched': 0 });
  const roundid = await roundIdOf(s, 'Lions');
  for (let heat = 1; heat <= 17; heat++) await s.post({ action: 'result.delete', roundid, heat });
  await s.post({ action: 'heat.select', roundid, heat: 1, now_racing: 0 });
  return roundid;
}

module.exports = { resetLions, Session, coordinator, raceHeat, assignAll, roundIdOf, sleep };

if (require.main === module) {
  (async () => {
    const [cmd, base = 'http://127.0.0.1:8771', arg] = process.argv.slice(2);
    if (cmd === 'reset-lions') {
      console.log('Lions & Tigers round', await resetLions(base), 'unraced');
    } else if (cmd === 'heat') {
      console.log(await raceHeat(base, Number(arg || 1)));
    } else if (cmd === 'reveal') {
      console.log(await assignAll(base, 'spike/standings',
        { key: process.env.KEY || '', reveal: arg === 'all' ? 'all' : Number(arg) }));
    } else if (cmd === 'page') {
      console.log(await assignAll(base, 'spike/' + arg, {}));
    } else {
      console.error('usage: driver.js heat|reveal|page [base] [arg]');
      process.exit(2);
    }
  })().catch(e => { console.error(e); process.exit(1); });
}
