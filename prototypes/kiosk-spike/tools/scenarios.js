// SPIKE #71: the scripted scenario for each page, shared by record.js and
// trace.js so the recordings and the frame numbers come from the same run.
'use strict';
const d = require('./driver.js');

// a0 is candidate A with the View Transitions API off (?vt=0).
const PAGES = {
  'now-racing': { a: '/spike/a/now-racing/', a0: '/spike/a/now-racing/?vt=0', b: '/spike/b/now-racing.html' },
  standings: { a: '/spike/a/standings/?address=', a0: '/spike/a/standings/?vt=0&address=', b: '/spike/b/standings.html?address=' },
  leaders: { a: '/spike/a/leaders/', a0: '/spike/a/leaders/?vt=0', b: '/spike/b/leaders.html' },
};
const CANDIDATES = (process.env.CANDS || 'a,a0,b').split(',');

// Lions & Tigers heats 1..17 are unraced after the fixture; take turns so
// a rerun does not show results already on the board.
let nextHeat = 1;

async function before(name, base, s, address) {
  if (name === 'standings') {
    await d.assignAll(base, 'spike/standings', { key: 'st-r2', reveal: 0 }, s);
  }
  if (name === 'now-racing') {
    const roundid = await d.roundIdOf(s, 'Lions');
    nextHeat = nextHeat > 15 ? 1 : nextHeat;
    await s.post({ action: 'result.delete', roundid, heat: nextHeat });
    await s.post({ action: 'heat.select', roundid, heat: nextHeat, now_racing: 0 });
  }
}

// Runs once the page is open.  Each scenario lasts about 30 s.
async function run(name, base, s) {
  if (name === 'now-racing') {
    await d.sleep(2000);
    for (let i = 0; i < 2; i++) {
      // The lineup on screen gets its results, lingers 4 s, then the next
      // lineup slides in.
      await d.raceHeat(base, nextHeat, { s, stageMs: 1500, runMs: 1500 });
      nextHeat++;
      await d.sleep(8000);
    }
  } else if (name === 'standings') {
    await d.sleep(500);
    // Reveal the whole class, one row every 2 s, from last place up.
    await d.assignAll(base, 'spike/standings', { key: 'st-r2', reveal: 'all' }, s);
    await d.sleep(5000 + 13 * 2000 + 1500);
  } else if (name === 'leaders') {
    await d.sleep(20000);
  }
}

// Injected before any page script: samples every animation frame, noting
// whether an animation (CSS, Web Animations or a view transition) runs.
const FRAME_SAMPLER = `
  window.__frames = [];
  (function f(t) {
    var anim = document.getAnimations().some(function (a) { return a.playState === 'running'; });
    window.__frames.push([t, anim ? 1 : 0]);
    requestAnimationFrame(f);
  })(performance.now());
`;

// Frame intervals while something animates. A 60 Hz display shows a frame
// every 16.7 ms; an interval of n vsyncs means n-1 frames were missed.
function frameStats(frames) {
  const VSYNC = 1000 / 60;
  let animFrames = 0, missed = 0, long = 0, animMs = 0;
  const intervals = [];
  for (let i = 1; i < frames.length; i++) {
    if (!frames[i][1] && !frames[i - 1][1]) continue;
    const dt = frames[i][0] - frames[i - 1][0];
    intervals.push(dt);
    animFrames++;
    animMs += dt;
    const n = Math.round(dt / VSYNC);
    if (n > 1) missed += n - 1;
    if (dt > 1.5 * VSYNC) long++;
  }
  intervals.sort((x, y) => x - y);
  const pct = p => intervals.length ? intervals[Math.min(intervals.length - 1, Math.floor(p * intervals.length))] : 0;
  return {
    animated_s: +(animMs / 1000).toFixed(1),
    frames: animFrames,
    fps: animMs ? +(animFrames / (animMs / 1000)).toFixed(1) : 0,
    missed_vsyncs: missed,
    missed_pct: animFrames + missed ? +(100 * missed / (animFrames + missed)).toFixed(1) : 0,
    long_frames: long,
    p50_ms: +pct(0.5).toFixed(1),
    p95_ms: +pct(0.95).toFixed(1),
    max_ms: +pct(1).toFixed(1),
  };
}

module.exports = { PAGES, CANDIDATES, before, run, FRAME_SAMPLER, frameStats };
