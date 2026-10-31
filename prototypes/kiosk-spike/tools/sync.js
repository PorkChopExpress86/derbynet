// SPIKE #71: how far apart two TVs show the same animation step.  Two
// browser instances (separate contexts, as two Pis would be) open the same
// page, started at random moments so their poll cycles are out of phase, as
// TVs switched on at different times are.  The script then drives the steps
// through the legacy API and reads, from each instance, the moment (epoch ms,
// one clock) it started each step's animation.
//   node tools/sync.js [base]                 writes results/sync.json
// Modes: "poll" (the legacy contracts: poll 500 ms after each response for now
// racing, poll.kiosk every 5 s for standings) and "push" (?push=1, a
// server-sent event from /spike/events starts the same fetch at once).
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const d = require('./driver.js');
const sc = require('./scenarios.js');

const BASE = process.argv[2] || 'http://127.0.0.1:8771';
const HEATS = Number(process.env.HEATS || 6);
const STEPS = Number(process.env.STEPS || 13);

const marks = (page, ev) => page.evaluate(e => window.__spike.log.filter(x => x.ev === e), ev);

function stats(xs) {
  const v = xs.filter(x => x != null).sort((a, b) => a - b);
  if (!v.length) return null;
  const mean = v.reduce((a, b) => a + b, 0) / v.length;
  return { n: v.length, mean: Math.round(mean), median: Math.round(v[Math.floor(v.length / 2)]),
           max: Math.round(v[v.length - 1]) };
}

async function openTwo(browser, url) {
  const pages = [];
  for (let i = 0; i < 2; i++) {
    const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    const page = await ctx.newPage();
    page.__reqs = [];
    page.on('request', r => { if (/action\.php|now-racing\/board/.test(r.url())) page.__reqs.push(Date.now()); });
    await d.sleep(Math.random() * 5000);   // a random phase for each TV
    await page.goto(url.replace('ADDR', `sync-${i}-${Date.now()}`));
    pages.push(page);
  }
  return pages;
}

async function nowRacing(browser, cand, push, s) {
  const roundid = await d.roundIdOf(s, 'Lions');
  for (let h = 1; h <= HEATS + 1; h++) await s.post({ action: 'result.delete', roundid, heat: h });
  await s.post({ action: 'heat.select', roundid, heat: 1, now_racing: 0 });
  let url = BASE + sc.PAGES['now-racing'][cand];
  if (push) url += (url.includes('?') ? '&' : '?') + 'push=1';
  const pages = await openTwo(browser, url);
  await d.sleep(3000);
  const gapNow = () => {
    const a = pages[0].__reqs.at(-1);
    const b = pages[1].__reqs.filter(x => x <= a + 600).at(-1);
    return Math.round(Math.abs(a - b));
  };
  if (process.env.DEBUG_GAP) console.log('idle poll phase gap', gapNow());
  const skew = [], latency = [], phase = [];
  for (let heat = 1; heat <= HEATS; heat++) {
    const { finishedAt } = await d.raceHeat(BASE, heat, { s, stageMs: 1500, runMs: 500 });
    const key = `${roundid}/${heat}`;
    const t = [];
    for (const p of pages) {
      await p.waitForFunction(k => window.__spike.log.some(x => x.ev === 'results' && x.detail === k), key, { timeout: 10000 }).catch(() => null);
      const m = (await marks(p, 'results')).find(x => x.detail === key);
      t.push(m ? m.t : null);
    }
    if (t.every(x => x != null)) {
      skew.push(Math.abs(t[0] - t[1]));
      latency.push(...t.map(x => x - finishedAt));
      // How far apart the two TVs' poll cycles were at FINISHED: the first
      // request each sent after it.
      const first = pages.map(p => p.__reqs.find(r => r > finishedAt));
      phase.push(Math.abs(first[0] - first[1]));
    }
    await d.sleep(5000);   // the linger, then the next lineup
  }
  const requests = await Promise.all(pages.map(p => p.evaluate(() => window.__spike.requests)));
  for (const p of pages) await p.context().close();
  return { skew_ms: stats(skew), latency_ms: stats(latency), requests_per_tv: requests,
           poll_phase_gap_ms: phase, samples: skew.map(Math.round) };
}

async function standings(browser, cand, push, s) {
  await d.assignAll(BASE, 'spike/standings', { key: 'st-r2', reveal: 0 }, s);
  let url = BASE + sc.PAGES.standings[cand] + 'ADDR';
  if (push) url += '&push=1';
  const pages = await openTwo(browser, url);
  // The new kiosks pick up the page and the class at their first polls.
  await d.assignAll(BASE, 'spike/standings', { key: 'st-r2', reveal: 0 }, s);
  await d.sleep(6000);
  const skew = [], latency = [];
  for (let n = 1; n <= STEPS; n++) {
    const at = await d.assignAll(BASE, 'spike/standings', { key: 'st-r2', reveal: n }, s);
    const t = [];
    for (const p of pages) {
      await p.waitForFunction(k => window.__spike.log.some(x => x.ev === 'reveal' && x.detail === k), n, { timeout: 8000 }).catch(() => null);
      const m = (await marks(p, 'reveal')).find(x => x.detail === n);
      t.push(m ? m.t : null);
    }
    if (t.every(x => x != null)) {
      skew.push(Math.abs(t[0] - t[1]));
      latency.push(...t.map(x => x - at));
    }
    await d.sleep(1000 + Math.random() * 1500);
  }
  for (const p of pages) await p.context().close();
  return { skew_ms: stats(skew), latency_ms: stats(latency), samples: skew.map(Math.round) };
}

(async () => {
  const browser = await chromium.launch();
  const s = await d.coordinator(BASE);
  const out = [];
  const cands = (process.env.CANDS || 'a,b').split(',');
  const modes = (process.env.MODES || 'poll,push').split(',');
  const which = (process.env.SYNC_PAGES || 'now-racing,standings').split(',');
  for (const push of modes.map(m => m === 'push')) {
    for (const cand of cands) {
      if (which.includes('now-racing')) {
        const nr = await nowRacing(browser, cand, push, s);
        const row1 = { candidate: cand, page: 'now-racing', mode: push ? 'push' : 'poll', ...nr };
        console.log(JSON.stringify(row1)); out.push(row1);
      }
      if (!which.includes('standings')) continue;
      const st = await standings(browser, cand, push, s);
      const row2 = { candidate: cand, page: 'standings', mode: push ? 'push' : 'poll', ...st };
      console.log(JSON.stringify(row2)); out.push(row2);
    }
  }
  await browser.close();
  const dest = path.join(__dirname, '..', 'results', process.env.OUT || 'sync.json');
  fs.writeFileSync(dest, JSON.stringify(out, null, 1));
  console.log('wrote', dest);
})().catch(e => { console.error(e); process.exit(1); });
