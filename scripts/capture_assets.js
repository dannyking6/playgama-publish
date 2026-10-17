#!/usr/bin/env node
/**
 * capture_assets.js — REAL-GAMEPLAY asset generator for Playgama.
 *
 * Launches the ACTUAL build being submitted, auto-plays through menus into real
 * gameplay, captures a burst of gameplay frames — no menu/loading frames, never
 * AI-drawn art — then lets capture_postprocess.py produce the three exact
 * Playgama cover specs:
 *     square 800x800 | portrait 1080x1920 | landscape 1920x1080
 *
 * Usage:
 *   xvfb-run -a node scripts/capture_assets.js --zip build-bridged.zip --out-dir ./assets
 * Options:
 *   --zip PATH | --dir PATH   build to capture (index.html at root)
 *   --out-dir PATH            output dir (default ./assets)
 *   --width/--height          gameplay viewport (default 1280x720 landscape build;
 *                             use 450x800 for portrait builds)
 *   --settle 8                seconds of active play before capturing
 *   --frames 40               number of gameplay frames
 *   --interval 800            ms between frames
 *
 * Requirements: npm i playwright && npx playwright install chromium ; pip install pillow
 */
'use strict';
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const http = require('http');

const log = (...a) => console.log('[CAPTURE]', ...a);

function parseArgs() {
  const a = {};
  for (let i = 2; i < process.argv.length; i++) {
    if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[i + 1];
  }
  return a;
}
const args = parseArgs();

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.svg': 'image/svg+xml', '.webp': 'image/webp',
  '.webm': 'video/webm', '.mp4': 'video/mp4', '.mp3': 'audio/mpeg',
  '.ogg': 'audio/ogg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
  '.json': 'application/json', '.wasm': 'application/wasm',
  '.ttf': 'font/ttf', '.woff': 'font/woff', '.woff2': 'font/woff2',
  '.xml': 'text/xml', '.ico': 'image/x-icon',
};
function serve(dir, port) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let p;
      try { p = decodeURIComponent(req.url.split('?')[0]); } catch (e) { p = '/'; }
      if (p.endsWith('/')) p += 'index.html';
      const fp = path.join(dir, p);
      fs.readFile(fp, (err, data) => {
        if (err) { res.statusCode = 404; res.end('not found'); return; }
        res.setHeader('Content-Type', MIME[path.extname(fp).toLowerCase()] || 'application/octet-stream');
        res.end(data);
      });
    });
    srv.listen(port, '127.0.0.1', () => resolve(srv));
  });
}

function unpack(zip, dst) {
  fs.mkdirSync(dst, { recursive: true });
  try {
    execFileSync('unzip', ['-o', '-q', zip, '-d', dst], { stdio: 'inherit' });
    return dst;
  } catch (e) {
    log('unzip unavailable — python3 zipfile fallback');
    execFileSync('python3', ['-c',
      'import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', zip, dst]);
    return dst;
  }
}
function sanitizeUnpacked(dst) {
  const root = path.resolve(dst);
  const walk = (d) => {
    for (const name of fs.readdirSync(d)) {
      const p = path.join(d, name);
      if (!path.resolve(p).startsWith(root)) { fs.rmSync(p, { recursive: true, force: true }); continue; }
      if (fs.statSync(p).isDirectory()) walk(p);
    }
  };
  walk(root);
}

// auto-start: walk through start menus into gameplay
const START_TEXTS = [
  /^play$/i, /^start$/i, /^start game$/i, /^play now$/i, /^let'?s (go|play)/i,
  /^go$/i, /^begin$/i, /^continue$/i, /tap to (play|start)/i,
  /click (or tap )?to (play|start)/i, /^jouer$/i, /^jugar$/i, /^gioca$/i,
  /^english$/i, /^français$/i, /^español$/i,
];
async function autostart(page) {
  for (let round = 0; round < 5; round++) {
    for (const re of START_TEXTS) {
      try {
        const loc = page.locator('button, a, div[role="button"], span, p, ion-button').filter({ hasText: re }).first();
        if (await loc.isVisible({ timeout: 150 }).catch(() => false)) {
          await loc.click({ timeout: 1500, force: true }).catch(() => {});
          await page.waitForTimeout(900);
          break;
        }
      } catch (e) {}
    }
    try {
      const canvas = page.locator('canvas, video, body').first();
      if (await canvas.isVisible({ timeout: 100 }).catch(() => false)) {
        await canvas.click({ position: { x: 10, y: 10 }, force: true, timeout: 1500 }).catch(() => {});
      }
    } catch (e) {}
    await page.keyboard.press('Enter').catch(() => {});
    await page.keyboard.press('Space').catch(() => {});
    await page.waitForTimeout(1200);
    log(`autostart round ${round + 1}: url=${page.url()}`);
  }
}

(async () => {
  const outDir = args['out-dir'] || './assets';
  const width = parseInt(args.width || '1280', 10);
  const height = parseInt(args.height || '720', 10);
  const settleS = parseFloat(args.settle || '8');
  const frames = parseInt(args.frames || '40', 10);
  const interval = parseInt(args.interval || '800', 10);

  let gameDir = args.dir;
  let tmpUnzip = null;
  if (!gameDir && args.zip) gameDir = tmpUnzip = unpack(args.zip, path.join(path.dirname(path.resolve(args.zip)), '.capture_unpacked'));
  if (!gameDir) throw new Error('provide --zip or --dir');
  sanitizeUnpacked(gameDir);
  if (!fs.existsSync(path.join(gameDir, 'index.html'))) throw new Error('index.html not at ' + gameDir + ' root');

  fs.mkdirSync(outDir, { recursive: true });
  const framesDir = path.join(outDir, '.frames');
  fs.rmSync(framesDir, { recursive: true, force: true });
  fs.mkdirSync(framesDir, { recursive: true });

  const port = 18000 + Math.floor(Math.random() * 2000);
  const srv = await serve(path.resolve(gameDir), port);
  const url = `http://127.0.0.1:${port}/index.html`;
  log('serving', url);

  const browser = await chromium.launch({ headless: false, args: ['--disable-blink-features=AutomationControlled'] });
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(5000);

  await autostart(page);

  // settle: keep playing so menus/loading die behind us
  const settleEnd = Date.now() + settleS * 1000;
  log(`getting deeper into gameplay for ${settleS}s...`);
  while (Date.now() < settleEnd) {
    try {
      const canvas = page.locator('canvas, video, body').first();
      if (await canvas.isVisible({ timeout: 80 }).catch(() => false)) {
        await canvas.click({ position: { x: width - 20, y: height - 20 }, force: true, timeout: 800 }).catch(() => {});
      }
    } catch (e) {}
    await page.keyboard.press('ArrowRight').catch(() => {});
    await page.waitForTimeout(700);
  }

  // capture burst — clip to the largest canvas (the real game viewport)
  const clip = await page.evaluate(() => {
    let best = null;
    for (const c of document.querySelectorAll('canvas, video')) {
      const r = c.getBoundingClientRect();
      if (!best || (r.width * r.height) > (best.w * best.h)) best = { w: r.width, h: r.height, x: r.x, y: r.y };
    }
    return best ? { x: Math.max(0, best.x), y: Math.max(0, best.y), width: Math.max(1, best.width), height: Math.max(1, best.height) } : null;
  }).catch(() => null);
  log('capture clip:', clip || 'full page');

  for (let i = 0; i < frames; i++) {
    const fp = path.join(framesDir, `frame_${String(i).padStart(3, '0')}.png`);
    await page.screenshot({ path: fp, ...(clip && clip.width > 8 && clip.height > 8 ? { clip } : {}) });
    await page.waitForTimeout(interval);
    if (i % 5 === 4) log(`captured ${i + 1}/${frames}`);
    if (i % 6 === 5) { await page.keyboard.press('ArrowLeft').catch(() => {}); await page.keyboard.press('Space').catch(() => {}); }
  }
  await browser.close();
  srv.close();

  log('post-processing frames with PIL...');
  execFileSync('python3', [path.join(__dirname, 'capture_postprocess.py'), framesDir, '--out-dir', outDir], { stdio: 'inherit' });
  log('✅ covers written to', outDir);
  if (tmpUnzip) fs.rmSync(tmpUnzip, { recursive: true, force: true });
})().catch((e) => { console.error('[CAPTURE][ERR]', e); process.exit(1); });
