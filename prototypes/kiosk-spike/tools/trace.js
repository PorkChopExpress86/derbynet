// SPIKE #71: frame timing for each candidate and page, from a Chromium
// performance trace (the frames the compositor reported presented, dropped or
// partial, as the Performance panel counts them) and from an in-page
// requestAnimationFrame sampler.  Run under xvfb-run for a headed browser:
//   xvfb-run -a -s "-screen 0 1920x1080x24" node tools/trace.js [base] [throttle...]
// e.g. throttles "1 4" runs each scenario unthrottled and with the CPU slowed
// 4x through DevTools emulation.  Writes results/frames.json.
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const d = require('./driver.js');
const sc = require('./scenarios.js');

const BASE = process.argv[2] || 'http://127.0.0.1:8771';
const THROTTLES = (process.argv.slice(3).length ? process.argv.slice(3) : ['1']).map(Number);
const HERE = path.join(__dirname, '..');
const TRACES = path.join(HERE, 'traces');
const ONLY = process.env.ONLY ? process.env.ONLY.split(',') : null;

// PipelineReporter is the per-frame record the Performance panel's Frames
// track is drawn from: each frame is presented, partially presented or
// dropped.  "Animating" frames are those the compositor flagged as carrying a
// main-thread or compositor animation.
function traceFrames(file) {
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  const events = raw.traceEvents || raw;
  const all = {}, anim = {};
  for (const e of events) {
    if (e.name !== 'PipelineReporter' || e.ph !== 'b') continue;
    const r = (e.args && (e.args.frame_reporter || e.args.chrome_frame_reporter)) || {};
    const st = (r.state || 'unknown').replace('STATE_', '');
    all[st] = (all[st] || 0) + 1;
    if (r.has_main_animation || r.has_compositor_animation) anim[st] = (anim[st] || 0) + 1;
  }
  const summary = c => {
    const shown = (c.PRESENTED_ALL || 0) + (c.PRESENTED_PARTIAL || 0);
    const dropped = c.DROPPED || 0;
    return { presented: c.PRESENTED_ALL || 0, partial: c.PRESENTED_PARTIAL || 0, dropped,
             dropped_pct: shown + dropped ? +(100 * dropped / (shown + dropped)).toFixed(1) : null };
  };
  return { animating: summary(anim), all: summary(all), states: all };
}

(async () => {
  fs.mkdirSync(TRACES, { recursive: true });
  fs.mkdirSync(path.join(HERE, 'results'), { recursive: true });
  const out = [];
  const s = await d.coordinator(BASE);
  for (const throttle of THROTTLES) {
    for (const name of Object.keys(sc.PAGES)) {
      for (const cand of sc.CANDIDATES) {
        if (ONLY && !ONLY.includes(name)) continue;
        const browser = await chromium.launch({
          headless: false,
          args: ['--window-size=1920,1080', '--window-position=0,0', '--kiosk'],
        });
        const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
        await ctx.addInitScript(sc.FRAME_SAMPLER);
        const page = await ctx.newPage();
        if (throttle > 1) {
          const cdp = await ctx.newCDPSession(page);
          await cdp.send('Emulation.setCPUThrottlingRate', { rate: throttle });
        }
        const address = `trace-${cand}`;
        await sc.before(name, BASE, s, address);
        const file = path.join(TRACES, `${cand}-${name}-x${throttle}.json`);
        await browser.startTracing(page, {
          path: file,
          categories: ['devtools.timeline', 'disabled-by-default-devtools.timeline',
                       'disabled-by-default-devtools.timeline.frame', 'cc', 'viz', 'benchmark'],
        });
        let url = BASE + sc.PAGES[name][cand];
        if (name === 'standings') url += address;
        await page.goto(url);
        await sc.run(name, BASE, s);
        const frames = await page.evaluate(() => window.__frames);
        const log = await page.evaluate(() => window.__spike.log.map(x => x.ev));
        await browser.stopTracing();
        await browser.close();
        const row = { candidate: cand, page: name, cpu_throttle: throttle,
                      raf: sc.frameStats(frames), trace: traceFrames(file),
                      marks: log.length };
        console.log(JSON.stringify(row));
        out.push(row);
      }
    }
  }
  const dest = path.join(HERE, 'results', process.env.OUT || 'frames.json');
  fs.writeFileSync(dest, JSON.stringify(out, null, 1));
  console.log('wrote', dest);
})().catch(e => { console.error(e); process.exit(1); });
