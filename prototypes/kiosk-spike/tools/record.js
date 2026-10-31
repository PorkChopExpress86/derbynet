// SPIKE #71: Playwright video of each candidate and page at 1920x1080, the
// same scenarios as trace.js.  Video encoding costs CPU, so the frame numbers
// come from trace.js runs, not these.  Run under xvfb-run:
//   xvfb-run -a -s "-screen 0 1920x1080x24" node tools/record.js [base]
// Writes recordings/<candidate>-<page>.webm.
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const d = require('./driver.js');
const sc = require('./scenarios.js');

const BASE = process.argv[2] || 'http://127.0.0.1:8771';
const OUT = path.join(__dirname, '..', 'recordings');
const ONLY = process.env.ONLY ? process.env.ONLY.split(',') : null;

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const s = await d.coordinator(BASE);
  for (const name of Object.keys(sc.PAGES)) {
    for (const cand of sc.CANDIDATES) {
      if (ONLY && !ONLY.includes(name)) continue;
      const browser = await chromium.launch({ headless: false, args: ['--window-size=1920,1080', '--kiosk'] });
      const ctx = await browser.newContext({
        viewport: { width: 1920, height: 1080 },
        recordVideo: { dir: OUT, size: { width: 1920, height: 1080 } },
      });
      const page = await ctx.newPage();
      const address = `rec-${cand}`;
      await sc.before(name, BASE, s, address);
      let url = BASE + sc.PAGES[name][cand];
      if (name === 'standings') url += address;
      await page.goto(url);
      await sc.run(name, BASE, s);
      const video = page.video();
      await ctx.close();
      await browser.close();
      const dest = path.join(OUT, `${cand}-${name}.webm`);
      fs.renameSync(await video.path(), dest);
      console.log(dest, (fs.statSync(dest).size / 1e6).toFixed(1) + ' MB');
    }
  }
})().catch(e => { console.error(e); process.exit(1); });
