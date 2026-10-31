// Screenshot each #39 prototype at 390x844 in light and dark, default view and one open sheet,
// then measure tap targets and run axe-core on the same state. Writes screenshots/*.png and results.json.
//   node shoot.js            (after ./build.sh; uses the preinstalled Playwright Chromium)
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const HERE = __dirname;
const DIST = path.join(HERE, 'dist');
const SHOTS = path.join(HERE, 'screenshots');
const AXE = path.join(HERE, '.cache/node_modules/axe-core/axe.min.js');

// Each layout and the clicks that open its sheet.
const LAYOUTS = [
  { page: 'dashboard-a', sheet: 'round', open: ['[data-open-round]'] },
  { page: 'dashboard-b', sheet: 'newround', open: ['[data-tab-rounds]', '[data-open-newround]'] },
  { page: 'dashboard-c', sheet: 'heat', open: ['[data-open-heat]'] },
  { page: 'checkin-a', sheet: 'racer', open: ['[data-open-racer]'] },
  { page: 'checkin-b', sheet: 'found', open: ['[data-open-racer]'] },
  { page: 'checkin-c', sheet: 'racer', open: ['[data-open-racer]'] },
];
const SCHEMES = ['light', 'dark'];

const TYPES = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' };

// Static server. Polls under /poll/ answer 204, the "state unchanged" reply the spec uses.
function serve() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname.startsWith('/poll/')) { res.writeHead(204); return res.end(); }
    const file = path.join(DIST, path.normalize(url.pathname === '/' ? '/index.html' : url.pathname));
    if (!file.startsWith(DIST) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

// Runs in the page: every operable element outside inert content, with its rendered box.
// A radio or checkbox wrapped in a <label> is measured by the label, which is what a finger hits.
function measureTargets() {
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=switch], [role=tab], [role=button], [tabindex]:not([tabindex="-1"])';
  const seen = new Set();
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (el.closest('[inert]') || el.closest('.sr-only')) continue;
    let target = el;
    if (el.matches('input[type=radio], input[type=checkbox]') && el.closest('label')) target = el.closest('label');
    if (seen.has(target)) continue;
    seen.add(target);
    const r = target.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || getComputedStyle(target).visibility === 'hidden') continue;
    const name = (el.getAttribute('aria-label') || target.textContent || el.getAttribute('placeholder') || el.id || '')
      .replace(/\s+/g, ' ').trim().slice(0, 40);
    out.push({ tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '', name,
      w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10 });
  }
  return out;
}

function summarizeTargets(list) {
  const sorted = [...list].sort((a, b) => Math.min(a.w, a.h) - Math.min(b.w, b.h) || a.w * a.h - b.w * b.h);
  return {
    count: list.length,
    smallest: sorted[0],
    under44: list.filter((t) => t.w < 44 || t.h < 44),
    under24: list.filter((t) => t.w < 24 || t.h < 24).length,
  };
}

async function runAxe(page) {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] },
      resultTypes: ['violations', 'incomplete'],
    });
    const brief = (v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length,
      wcag: v.tags.some((t) => /^wcag2/.test(t)), help: v.help,
      targets: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) });
    return { version: window.axe.version, violations: r.violations.map(brief), incomplete: r.incomplete.map(brief) };
  });
}

async function capture(page, file) {
  await page.screenshot({ path: path.join(SHOTS, file) });
  const targets = summarizeTargets(await page.evaluate(measureTargets));
  const axe = await runAxe(page);
  return { file: `screenshots/${file}`, targets, axe };
}

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const server = await serve();
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  const results = [];
  try {
    for (const layout of LAYOUTS) {
      for (const scheme of SCHEMES) {
        const context = await browser.newContext({
          viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
          colorScheme: scheme, reducedMotion: 'reduce',
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', (e) => errors.push(String(e)));
        page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
        await page.goto(`${base}/${layout.page}.html`);
        await page.waitForFunction(() => window.Alpine && window.htmx && document.body._x_dataStack);
        await page.evaluate(() => document.fonts.ready);

        const entry = { layout: layout.page, scheme, sheet: layout.sheet };
        // Layout metric at scroll top: height of fixed or sticky chrome ([data-chrome]), the space left
        // for scrolling content, and how many racer rows (rows with a pass switch) are fully visible.
        entry.layoutMetric = await page.evaluate(() => {
          const chrome = [...document.querySelectorAll('[data-chrome]')].reduce((s, el) => s + el.getBoundingClientRect().height, 0);
          const nav = document.querySelector('nav[aria-label=Primary]').getBoundingClientRect().top;
          const rows = [...document.querySelectorAll('main li')].filter((li) => li.querySelector('[role=switch]')
            && !li.closest('[data-chrome]')).filter((li) => { const r = li.getBoundingClientRect(); return r.top >= 0 && r.bottom <= nav; });
          return { chromePx: Math.round(chrome), contentPx: Math.round(window.innerHeight - chrome), racerRowsVisible: rows.length };
        });
        entry.default = await capture(page, `${layout.page}-${scheme}-default.png`);
        for (const sel of layout.open) await page.tap(sel);
        await page.locator(`[data-sheet="${layout.sheet}"]`).waitFor({ state: 'visible' });
        await page.waitForTimeout(150);
        entry.focusInSheet = await page.evaluate((s) => !!document.activeElement.closest(`[data-sheet="${s}"]`), layout.sheet);
        entry.sheetState = await capture(page, `${layout.page}-${scheme}-sheet.png`);
        entry.errors = errors;
        results.push(entry);
        console.log(`${layout.page} ${scheme}: default ${entry.default.axe.violations.length} violations, `
          + `min ${entry.default.targets.smallest.w}x${entry.default.targets.smallest.h}; sheet ${entry.sheetState.axe.violations.length}, `
          + `min ${entry.sheetState.targets.smallest.w}x${entry.sheetState.targets.smallest.h}; errors ${errors.length}`);
        await context.close();
      }
    }
  } finally {
    await browser.close();
    server.close();
  }
  fs.writeFileSync(path.join(HERE, 'results.json'), JSON.stringify(results, null, 2) + '\n');
})().catch((e) => { console.error(e); process.exit(1); });
