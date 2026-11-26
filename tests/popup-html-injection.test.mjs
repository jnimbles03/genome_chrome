/**
 * HTML injection in the extension popup PDF list.
 *
 * These tests fail while link text from a web page is inserted into the popup
 * with innerHTML. They pass once that text is rendered as text and cannot
 * change the popup's markup or CSS.
 *
 * Run:
 *   node --test --test-timeout=120000 tests/popup-html-injection.test.mjs
 *
 * FormGenome_Extension_Firefox is not covered. Its popup/popup.js stops at the
 * update-notification handler and never defines renderPdfList, so this sink
 * is not in that copy. reportGenerator.js (downloaded HTML report) and the
 * dashboard tables are separate sinks and are not exercised here.
 */

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { extname, join, normalize, sep } from 'node:path';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));

const MARKUP_TEXT = [
  '<style>#pdfList,.header{display:none}</style>',
  '<h2 id="injected-heading">Form Genome update required</h2>',
  '<a id="injected-link" href=https://attacker.example/formgenome-1.3.zip target=_blank>Download v1.3</a>',
].join('');

const QUOTE_TEXT = '"><img id="attr-break" src="x">';
const BENIGN_TEXT = "W-4 Employee's Withholding Certificate";

const EXTENSIONS = [
  { name: 'root', dir: '' },
  { name: 'extracted_chrome_share', dir: 'extracted_chrome_share' },
  { name: 'FormGenome_Extension_Batch_Fix', dir: 'FormGenome_Extension_Batch_Fix' },
  { name: 'FormGenome_Extension_Clean', dir: 'FormGenome_Extension_Clean' },
  { name: 'FormGenome_Extension_Data_Fix', dir: 'FormGenome_Extension_Data_Fix' },
  { name: 'FormGenome_Extension_Final_Debug', dir: 'FormGenome_Extension_Final_Debug' },
  { name: 'FormGenome_Extension_Final_Fixed', dir: 'FormGenome_Extension_Final_Fixed' },
  { name: 'FormGenome_Extension_Normalization_Fix', dir: 'FormGenome_Extension_Normalization_Fix' },
];

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

function escapeText(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function attackerHtml() {
  return `<!DOCTYPE html>
<html>
<body>
<a id="malicious" href="/forms/a.pdf">${escapeText(MARKUP_TEXT)}</a>
<a id="quoted" href="/forms/b.pdf">${escapeText(QUOTE_TEXT)}</a>
<a id="benign" href="/forms/w4.pdf">${escapeText(BENIGN_TEXT)}</a>
</body>
</html>`;
}

function contentStub() {
  return `window.__messageListeners = [];
window.chrome = {
  runtime: {
    lastError: undefined,
    sendMessage() {},
    getURL(path) { return path; },
    onMessage: {
      addListener(fn) { window.__messageListeners.push(fn); }
    }
  }
};
window.browser = window.chrome;`;
}

function popupStub(scanResponse) {
  return `window.__scanResponse = ${JSON.stringify(scanResponse)};
window.chrome = {
  runtime: {
    lastError: undefined,
    sendMessage() {},
    getURL(path) { return path; },
    onMessage: { addListener() {} }
  },
  tabs: {
    async query() { return [{ id: 1, url: ${JSON.stringify('http://attacker.test/')} }]; },
    async sendMessage() { return structuredClone(window.__scanResponse); },
    create() {}
  },
  storage: {
    local: {
      get(keys, callback) {
        const value = {};
        const result = Promise.resolve(value);
        if (typeof callback === 'function') result.then(callback);
        return result;
      },
      set() { return Promise.resolve(); }
    }
  }
};
window.browser = window.chrome;`;
}

function startServer() {
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://127.0.0.1');
      if (url.pathname === '/attacker.html') {
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
        res.end(attackerHtml());
        return;
      }
      const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
      const filePath = normalize(join(repoRoot, relative));
      const rootPrefix = repoRoot.endsWith(sep) ? repoRoot : repoRoot + sep;
      if (!filePath.startsWith(rootPrefix)) {
        res.writeHead(403);
        res.end('forbidden');
        return;
      }
      const body = await readFile(filePath);
      res.writeHead(200, { 'content-type': MIME[extname(filePath)] || 'application/octet-stream' });
      res.end(body);
    } catch {
      res.writeHead(404);
      res.end('not found');
    }
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function startChrome(userDataDir, debugPort) {
  const chrome = spawn('google-chrome', [
    '--headless=new',
    '--disable-gpu',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    `--user-data-dir=${userDataDir}`,
    `--remote-debugging-port=${debugPort}`,
    'about:blank',
  ], { stdio: 'ignore' });
  return chrome;
}

async function waitForCdp(debugPort) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`);
      if (response.ok) return response.json();
    } catch {
      // Chrome is still starting.
    }
    await delay(100);
  }
  throw new Error('Chrome DevTools did not start');
}

function openCdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let nextId = 0;
  const pending = new Map();
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message || JSON.stringify(message.error)));
    else resolve(message.result);
  });
  const ready = new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve);
    ws.addEventListener('error', reject);
  });
  function send(method, params = {}, sessionId) {
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      const payload = { id, method, params };
      if (sessionId) payload.sessionId = sessionId;
      ws.send(JSON.stringify(payload));
    });
  }
  return { ws, ready, send };
}

async function attach(browser, targetId) {
  const { sessionId } = await browser.send('Target.attachToTarget', { targetId, flatten: true });
  await browser.send('Page.enable', {}, sessionId);
  await browser.send('Runtime.enable', {}, sessionId);
  return sessionId;
}

async function evaluate(browser, sessionId, expression) {
  const result = await browser.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  }, sessionId);
  if (result.exceptionDetails) {
    const text = result.exceptionDetails.exception?.description
      || result.exceptionDetails.text
      || 'page evaluation failed';
    throw new Error(text);
  }
  return result.result.value;
}

async function navigate(browser, sessionId, url) {
  await browser.send('Page.navigate', { url }, sessionId);
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const state = await evaluate(browser, sessionId, 'document.readyState');
    const href = await evaluate(browser, sessionId, 'location.href');
    if (state === 'complete' && href.startsWith(url.split('?')[0])) return;
    await delay(50);
  }
  throw new Error(`navigation timed out for ${url}`);
}

test('page link text is rendered as text in every popup PDF list', async (t) => {
  const server = await startServer();
  const origin = `http://127.0.0.1:${server.address().port}`;
  const userDataDir = await mkdtemp(join(tmpdir(), 'form-genome-popup-'));
  const debugPort = server.address().port + 1;
  const chrome = startChrome(userDataDir, debugPort);
  let browser;
  try {
    const version = await waitForCdp(debugPort);
    browser = openCdp(version.webSocketDebuggerUrl);
    await browser.ready;
    const { targetId } = await browser.send('Target.createTarget', { url: 'about:blank' });
    const pageSession = await attach(browser, targetId);

    for (const extension of EXTENSIONS) {
      await t.test(extension.name, async () => {
        const prefix = extension.dir ? `/${extension.dir}` : '';
        await navigate(browser, pageSession, `${origin}/attacker.html?ext=${encodeURIComponent(extension.name)}`);
        const scan = await evaluate(browser, pageSession, `(async () => {
          ${contentStub()}
          await new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = ${JSON.stringify(`${origin}${prefix}/content/content.js`)};
            script.onload = () => resolve();
            script.onerror = () => reject(new Error('content script failed to load'));
            document.documentElement.appendChild(script);
          });
          const pageText = document.getElementById('malicious').textContent;
          if (!window.__messageListeners.length) throw new Error('content script registered no listener');
          const response = await new Promise((resolve, reject) => {
            let finished = false;
            const keepOpen = window.__messageListeners[0](
              { type: 'SCAN_FOR_PDFS' },
              {},
              (reply) => {
                finished = true;
                resolve(structuredClone(reply));
              }
            );
            if (keepOpen !== true && !finished) reject(new Error('SCAN_FOR_PDFS listener did not stay open'));
          });
          return { pageText, response };
        })()`);

        assert.equal(scan.pageText, MARKUP_TEXT);
        const forwarded = scan.response.pdfs.map((pdf) => pdf.text);
        assert.ok(
          forwarded.includes(MARKUP_TEXT),
          `content script did not forward link text: ${JSON.stringify(forwarded)}`
        );
        assert.ok(forwarded.includes(QUOTE_TEXT));
        assert.ok(forwarded.includes(BENIGN_TEXT));

        const popupSession = pageSession;
        const { identifier } = await browser.send('Page.addScriptToEvaluateOnNewDocument', {
          source: popupStub(scan.response),
        }, popupSession);
        try {
          await navigate(browser, popupSession, `${origin}${prefix}/popup/popup.html`);
          const rendered = await evaluate(browser, popupSession, `(() => {
            const header = document.querySelector('.header');
            const pdfList = document.getElementById('pdfList');
            const formLinks = [...document.querySelectorAll('#pdfItems a.form-link')].map((link) => ({
              text: link.textContent,
              href: link.getAttribute('href'),
              childElementCount: link.querySelectorAll('*').length
            }));
            return {
              itemCount: document.querySelectorAll('#pdfItems .pdf-item').length,
              injectedHeading: Boolean(document.getElementById('injected-heading')),
              injectedLink: Boolean(document.getElementById('injected-link')),
              attrBreak: Boolean(document.getElementById('attr-break')),
              styleCount: document.querySelectorAll('style').length,
              headerDisplay: header ? getComputedStyle(header).display : null,
              pdfListDisplay: pdfList ? getComputedStyle(pdfList).display : null,
              attackerHrefs: [...document.querySelectorAll('a')]
                .map((link) => link.getAttribute('href') || '')
                .filter((href) => href.includes('attacker.example')),
              formLinks,
              noPdf: document.getElementById('noPdf') ? document.getElementById('noPdf').className : null
            };
          })()`);

          const problems = [];
          if (rendered.injectedHeading) problems.push('link text was parsed into an h2');
          if (rendered.injectedLink) problems.push('link text was parsed into an anchor');
          if (rendered.attrBreak) problems.push('link text broke out of the title attribute');
          if (rendered.styleCount !== 0) problems.push(`link text created ${rendered.styleCount} style element(s)`);
          if (rendered.headerDisplay === 'none') problems.push('injected CSS hid the popup header');
          if (rendered.pdfListDisplay === 'none') problems.push('injected CSS hid the PDF list');
          if (rendered.attackerHrefs.length !== 0) problems.push(`attacker download link was rendered: ${rendered.attackerHrefs.join(', ')}`);
          if (!rendered.formLinks.some((link) => link.text === MARKUP_TEXT && link.childElementCount === 0)) {
            problems.push(`markup was not kept as text: ${JSON.stringify(rendered.formLinks)}`);
          }
          if (!rendered.formLinks.some((link) => link.text === QUOTE_TEXT && link.childElementCount === 0)) {
            problems.push(`quoted link text was not kept as text: ${JSON.stringify(rendered.formLinks)}`);
          }
          if (!rendered.formLinks.some((link) => link.text === BENIGN_TEXT)) {
            problems.push(`benign link text missing: ${JSON.stringify(rendered)}`);
          }
          assert.deepEqual(problems, [], problems.join('; '));
        } finally {
          await browser.send('Page.removeScriptToEvaluateOnNewDocument', { identifier }, popupSession);
        }
      });
    }
  } finally {
    if (browser) browser.ws.close();
    chrome.kill('SIGKILL');
    server.close();
    await delay(200);
    await rm(userDataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});
