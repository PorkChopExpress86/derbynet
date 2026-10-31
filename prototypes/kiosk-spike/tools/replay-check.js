// SPIKE #71: the replay iframe check.  Each page is framed the way
// website/replay.php frames kiosk.php (spike/replay-harness), and the parent
// posts 'replay-started' and 'replay-ended' into the iframe as replay.php's
// announce_to_interior() does.  Checks, per candidate:
//   - the page renders inside the frame (X-Frame-Options allows it);
//   - on replay-started, running animations pause and stay put, and no new
//     poll or animation starts while the replay shows, even though the timer
//     reports a heat (now racing) or the reveal is due (standings, leaders);
//   - on replay-ended, paused animations finish and the page catches up.
//   node tools/replay-check.js [base]      writes results/replay.json
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const d = require('./driver.js');
const sc = require('./scenarios.js');

const BASE = process.argv[2] || 'http://127.0.0.1:8771';

async function frameOf(page) {
  const handle = await page.waitForSelector('#interior');
  const frame = await handle.contentFrame();
  await frame.waitForFunction(() => window.__spike && window.__spike.log);
  return frame;
}

const state = frame => frame.evaluate(() => ({
  requests: window.__spike.requests,
  marks: window.__spike.log.map(x => x.ev),
  running: document.getAnimations().filter(a => a.playState === 'running').length,
  pausedAnims: document.getAnimations().filter(a => a.playState === 'paused').length,
  times: document.getAnimations().map(a => Math.round(a.currentTime || 0)),
}));

const since = (after, before) => after.marks.slice(before.marks.length);

async function check(browser, cand, name, s) {
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await ctx.newPage();
  const address = `replay-${cand}`;
  await sc.before(name, BASE, s, address);
  let src = sc.PAGES[name][cand] + (name === 'standings' ? address : '');
  await page.goto(BASE + '/spike/replay-harness?src=' + encodeURIComponent(src));
  const frame = await frameOf(page);
  const r = { candidate: cand, page: name };
  await d.sleep(2500);
  r.renders_in_frame = (await frame.evaluate(() => document.body.innerText.length)) > 20;

  // Start something animating, then cut to the replay part-way through.
  if (name === 'now-racing') {
    await d.raceHeat(BASE, 17, { s, stageMs: 1500, runMs: 300 });
    await frame.waitForFunction(() => window.__spike.log.some(x => x.ev === 'results'), null, { timeout: 5000 });
    await d.sleep(300);
  } else if (name === 'standings') {
    await d.assignAll(BASE, 'spike/standings', { key: 'st-r2', reveal: 'all' }, s);
    await frame.waitForFunction(() => window.__spike.log.filter(x => x.ev === 'reveal').length >= 2, null, { timeout: 12000 });
    await d.sleep(200);
  } else {
    await frame.waitForFunction(() => window.__spike.log.filter(x => x.ev === 'panel').length >= 2, null, { timeout: 15000 });
    await d.sleep(500);
  }
  await page.evaluate(() => start_replay());
  await d.sleep(100);
  const p0 = await state(frame);
  // While the replay shows: the timer reports another heat; reveals fall due.
  if (name === 'now-racing') {
    await d.raceHeat(BASE, 1, { s, stageMs: 300, runMs: 300 });
  }
  await d.sleep(name === 'leaders' ? 8000 : 5000);
  const p1 = await state(frame);
  r.paused_animations = p0.pausedAnims;
  r.running_while_paused = p1.running;
  r.animation_clock_moved_ms = Math.max(0, ...p1.times.map((t, i) => Math.abs(t - (p0.times[i] ?? t))));
  r.requests_while_paused = p1.requests - p0.requests;
  r.marks_while_paused = since(p1, p0).filter(m => m !== 'paused');

  await page.evaluate(() => end_replay());
  const t0 = Date.now();
  const caughtUp = { 'now-racing': 'lineup', standings: 'reveal', leaders: 'panel' }[name];
  await frame.waitForFunction(
    ([n, ev]) => window.__spike.log.slice(n).some(x => x.ev === ev),
    [p1.marks.length, caughtUp], { timeout: 15000 }).catch(() => null);
  r.resume_to_next_step_ms = Date.now() - t0;
  await d.sleep(1500);
  const p2 = await state(frame);
  r.marks_after_resume = since(p2, p1);
  r.paused_left_after_resume = p2.pausedAnims;
  r.pass = r.renders_in_frame && r.paused_animations > 0 && r.running_while_paused === 0
    && r.animation_clock_moved_ms < 50 && r.marks_while_paused.length === 0
    && (name !== 'now-racing' || r.requests_while_paused === 0)
    && r.marks_after_resume.includes(caughtUp) && r.paused_left_after_resume === 0;
  await ctx.close();
  return r;
}

(async () => {
  const browser = await chromium.launch();
  const s = await d.coordinator(BASE);
  const out = [];
  for (const name of Object.keys(sc.PAGES)) {
    for (const cand of sc.CANDIDATES) {
      const r = await check(browser, cand, name, s);
      console.log(JSON.stringify(r));
      out.push(r);
    }
  }
  await browser.close();
  const dest = path.join(__dirname, '..', 'results', 'replay.json');
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, JSON.stringify(out, null, 1));
  console.log('wrote', dest);
})().catch(e => { console.error(e); process.exit(1); });
