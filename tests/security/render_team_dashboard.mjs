// Load each real team-dashboard HTML file in headless Chrome.
// Fetch is replaced before page scripts run so the dashboard renders the
// JSON body produced by the API test, and the production host is never contacted.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

const WebSocket = globalThis.WebSocket;
const chromeBin = process.env.CHROME_BIN || 'google-chrome';
const repoRoot = process.argv[2];
const configPath = process.argv[3];
const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

function serve(root) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const rel = decodeURIComponent(url.pathname);
    const filePath = path.normalize(path.join(root, rel));
    if (!filePath.startsWith(root)) {
      res.writeHead(403);
      res.end('forbidden');
      return;
    }
    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(404);
        res.end('not found');
        return;
      }
      res.writeHead(200);
      res.end(data);
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function launchChrome(port, userDataDir) {
  const child = spawn(chromeBin, [
    '--headless=new',
    '--disable-gpu',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${userDataDir}`,
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
    'about:blank',
  ], { stdio: 'ignore' });
  return child;
}

async function waitForJson(url, attempts = 50) {
  let lastError;
  for (let i = 0; i < attempts; i++) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.json();
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw lastError || new Error(`timed out waiting for ${url}`);
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(JSON.stringify(message.error)));
        else resolve(message.result);
      }
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params });
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(payload);
    });
  }
}

function preamble(formsBody, victimUserId) {
  return `
    window.__fgRequests = [];
    window.alert = function () {};
    window.confirm = function () { return false; };
    const formsBody = ${JSON.stringify(formsBody)};
    const storage = {
      get: async function (key) {
        if (key === 'userId') return { userId: ${JSON.stringify(victimUserId)} };
        return {};
      },
      set: async function () {}
    };
    window.browser = { storage: { local: storage } };
    window.chrome = { storage: { local: storage } };
    window.fetch = async function (url) {
      const requested = String(url);
      window.__fgRequests.push(requested);
      if (!requested.includes('/api/team/forms')) {
        throw new Error('unexpected fetch ' + requested);
      }
      return {
        ok: true,
        status: 200,
        json: async function () { return formsBody; },
        text: async function () { return JSON.stringify(formsBody); }
      };
    };
  `;
}

async function inspectPage(cdp, pageUrl, formsBody, victimUserId, editAnalysisId) {
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
    source: preamble(formsBody, victimUserId),
  });
  await cdp.send('Page.navigate', { url: pageUrl });
  const started = Date.now();
  let view = null;
  let last = null;
  while (Date.now() - started < 8000) {
    const evaluated = await cdp.send('Runtime.evaluate', {
      expression: `(() => {
        const tbody = document.getElementById('formsTableBody');
        if (!tbody || tbody.children.length === 0) return null;
        const classes = [];
        document.querySelectorAll('[class]').forEach((el) => {
          (el.getAttribute('class') || '').split(/\\s+/).forEach((name) => {
            if (name.startsWith('attacker-')) classes.push(name);
          });
        });
        return {
          text: document.body.textContent,
          classes,
          requests: window.__fgRequests || [],
        };
      })()`,
      returnByValue: true,
    });
    last = evaluated.result?.value || evaluated;
    if (last && last.text) {
      view = last;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!view) {
    const err = await cdp.send('Runtime.evaluate', {
      expression: 'document.body ? document.body.innerText.slice(0, 500) : "no body"',
      returnByValue: true,
    });
    throw new Error(`dashboard did not render rows: ${JSON.stringify(err.result?.value)}`);
  }

  const edited = await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      const row = document.querySelector(${JSON.stringify(`tr[data-analysis-id="${editAnalysisId}"]`)});
      const button = row ? row.querySelector('.edit-btn') : null;
      if (!button) return { clicked: false, classes: [], inputs: {} };
      button.click();
      const classes = [];
      document.querySelectorAll('[class]').forEach((el) => {
        (el.getAttribute('class') || '').split(/\\s+/).forEach((name) => {
          if (name.startsWith('attacker-')) classes.push(name);
        });
      });
      const inputs = {};
      document.querySelectorAll('input.edit-field, select.edit-field').forEach((el) => {
        inputs[el.name] = el.value;
      });
      return { clicked: true, classes, inputs, text: document.body.textContent };
    })()`,
    returnByValue: true,
  });

  return {
    view,
    edit: edited.result.value,
  };
}

async function main() {
  const server = await serve(repoRoot);
  const port = server.address().port;
  const debugPort = 9300 + Math.floor(Math.random() * 500);
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fg-chrome-'));
  const chrome = launchChrome(debugPort, userDataDir);
  const results = {};
  try {
    const version = await waitForJson(`http://127.0.0.1:${debugPort}/json/version`);
    const browser = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      browser.addEventListener('open', resolve);
      browser.addEventListener('error', reject);
    });
    const browserCdp = new Cdp(browser);
    for (const htmlRel of config.pages) {
      const target = await browserCdp.send('Target.createTarget', { url: 'about:blank' });
      const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
      const page = targets.find((item) => item.id === target.targetId);
      const pageWs = new WebSocket(page.webSocketDebuggerUrl);
      await new Promise((resolve, reject) => {
        pageWs.addEventListener('open', resolve);
        pageWs.addEventListener('error', reject);
      });
      const pageCdp = new Cdp(pageWs);
      const pageUrl = `http://127.0.0.1:${port}/${htmlRel.split(path.sep).join('/')}`;
      try {
        results[htmlRel] = await inspectPage(
          pageCdp,
          pageUrl,
          config.formsBody,
          config.victimUserId,
          config.editAnalysisId,
        );
      } catch (error) {
        results[htmlRel] = { error: String(error && error.stack || error) };
      }
      pageWs.close();
      await browserCdp.send('Target.closeTarget', { targetId: target.targetId });
    }
    browser.close();
  } finally {
    chrome.kill('SIGKILL');
    server.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
  fs.writeFileSync(config.outputPath, JSON.stringify(results, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
