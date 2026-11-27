// Drives the real extension popup and dashboard pages in Chrome.
// Chrome storage is an in-page stand-in for chrome.storage.local. It keeps the
// JSON values the page writes and reads them back unchanged, which is the
// storage contract these pages use (get/set of JSON-serializable objects).

const fs = require('fs');
const puppeteer = require('puppeteer-core');

const POPUPS = [
  '/FormGenome_Extension_Final_Fixed/popup/popup.html',
  '/FormGenome_Extension_Data_Fix/popup/popup.html',
];

const PERSONAL_DASHBOARDS = [
  ['/FormGenome_Extension_Final_Fixed/dashboard/dashboard.html', 'card'],
  ['/FormGenome_Extension_Data_Fix/dashboard/dashboard.html', 'card'],
  ['/dashboard/dashboard.html', 'card'],
  ['/FormGenome_Extension_Final_Debug/dashboard/dashboard.html', 'card'],
  ['/FormGenome_Extension_Batch_Fix/dashboard/dashboard.html', 'card'],
  ['/FormGenome_Extension_Normalization_Fix/dashboard/dashboard.html', 'card'],
  ['/FormGenome_Extension_Clean/dashboard/dashboard.html', 'table'],
  ['/FormGenome_Extension_Firefox/dashboard/dashboard.html', 'table'],
  ['/extracted_chrome_share/dashboard/dashboard.html', 'table'],
];

const TEAM_DASHBOARDS = [
  '/FormGenome_Extension_Final_Fixed/dashboard/team-dashboard.html',
  '/FormGenome_Extension_Data_Fix/dashboard/team-dashboard.html',
  '/dashboard/team-dashboard.html',
  '/FormGenome_Extension_Final_Debug/dashboard/team-dashboard.html',
  '/FormGenome_Extension_Batch_Fix/dashboard/team-dashboard.html',
  '/FormGenome_Extension_Normalization_Fix/dashboard/team-dashboard.html',
  '/FormGenome_Extension_Clean/dashboard/team-dashboard.html',
  '/FormGenome_Extension_Firefox/dashboard/team-dashboard.html',
  '/extracted_chrome_share/dashboard/team-dashboard.html',
];

function arg(name) {
  const i = process.argv.indexOf(name);
  if (i < 0 || !process.argv[i + 1]) {
    throw new Error('missing ' + name);
  }
  return process.argv[i + 1];
}

async function launch(base, pdfUrl, teamFormsBody) {
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(20000);
  const blocked = [];
  const syncPayloads = [];
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    let url;
    try {
      url = new URL(req.url());
    } catch (err) {
      req.abort();
      return;
    }
    const local = url.hostname === '127.0.0.1' || url.hostname === 'localhost';
    if (local) {
      req.continue();
      return;
    }
    const cors = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };
    // The page is on 127.0.0.1 and the extension calls the configured API host.
    // Respond here so the request never leaves the machine. A JSON POST is not a
    // simple request, so the preflight has to succeed or the POST is never sent.
    if (url.pathname === '/api/team/sync') {
      if (req.method() === 'OPTIONS') {
        req.respond({ status: 204, headers: cors });
        return;
      }
      if (req.method() === 'POST') {
        try {
          syncPayloads.push(JSON.parse(req.postData() || '{}'));
        } catch (err) {
          syncPayloads.push({ parseError: String(err), raw: req.postData() });
        }
        req.respond({
          status: 200,
          contentType: 'application/json',
          headers: cors,
          body: JSON.stringify({ ok: true, message: 'Form synced successfully' }),
        });
        return;
      }
    }
    if (url.pathname === '/api/team/forms' && teamFormsBody) {
      req.respond({
        status: 200,
        contentType: 'application/json',
        headers: cors,
        body: teamFormsBody,
      });
      return;
    }
    blocked.push(url.href);
    req.abort();
  });

  await page.evaluateOnNewDocument((pdf) => {
    const KEY = 'fg-chrome-storage';
    function read() {
      try {
        return JSON.parse(localStorage.getItem(KEY) || '{}');
      } catch (err) {
        return {};
      }
    }
    function write(data) {
      localStorage.setItem(KEY, JSON.stringify(data));
    }
    function area() {
      return {
        get(keys, callback) {
          const data = read();
          let result;
          if (keys == null) result = JSON.parse(JSON.stringify(data));
          else if (typeof keys === 'string') result = { [keys]: data[keys] };
          else if (Array.isArray(keys)) {
            result = {};
            keys.forEach((k) => {
              result[k] = data[k];
            });
          } else result = JSON.parse(JSON.stringify(data));
          if (typeof callback === 'function') callback(result);
          return Promise.resolve(result);
        },
        set(obj) {
          const data = read();
          Object.assign(data, obj || {});
          write(data);
          return Promise.resolve();
        },
        remove(keys) {
          const data = read();
          [].concat(keys || []).forEach((k) => delete data[k]);
          write(data);
          return Promise.resolve();
        },
        clear() {
          write({});
          return Promise.resolve();
        },
      };
    }
    const storage = area();
    const chromeStub = {
      storage: {
        local: storage,
        sync: storage,
        onChanged: { addListener() {} },
      },
      tabs: {
        query() {
          return Promise.resolve([{ id: 1, url: pdf, active: true, windowId: 1 }]);
        },
        sendMessage() {
          return Promise.reject(new Error('no content script'));
        },
        get() {
          return Promise.resolve({ id: 1, url: pdf });
        },
        create() {
          return Promise.resolve();
        },
        onRemoved: { addListener() {} },
        onActivated: { addListener() {} },
      },
      runtime: {
        getURL(path) {
          return path;
        },
        onMessage: { addListener() {} },
      },
      action: {
        setBadgeText() {},
        setBadgeBackgroundColor() {},
      },
      downloads: { download() { return Promise.resolve(); } },
    };
    window.chrome = chromeStub;
    window.browser = chromeStub;
  }, pdfUrl || 'http://127.0.0.1/missing.pdf');

  return { browser, page, blocked, syncPayloads };
}

async function inspect(page) {
  return page.evaluate(() => {
    const images = [...document.querySelectorAll('img')].map((img) => ({
      id: img.id,
      src: img.getAttribute('src'),
    }));
    const styledAnchors = [...document.querySelectorAll('a')]
      .filter((a) => a.hasAttribute('style'))
      .map((a) => ({ href: a.getAttribute('href'), style: a.getAttribute('style') }));
    const hrefs = [...document.querySelectorAll('a')].map((a) => a.getAttribute('href'));
    const text = document.body ? document.body.innerText : '';
    return {
      sawTitle: text.includes('Benefits Enrollment'),
      images,
      styledAnchors,
      hrefs,
      textSample: text.slice(0, 600),
    };
  });
}

async function settlePersonal(page) {
  await page.waitForFunction(() => {
    const total = document.getElementById('totalForms');
    return total && total.textContent !== '0';
  });
  await page.evaluate(() => {
    const sort = document.getElementById('sortSelect');
    if (sort) {
      sort.value = 'name';
      sort.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const pill = document.querySelector('.filter-pill[data-filter="all"]');
    if (pill) pill.click();
  });
  await page.waitForFunction(() => document.body.innerText.includes('Benefits Enrollment'));
}

async function runPersonal(base, pdfUrl, outPath) {
  const { browser, page, blocked, syncPayloads } = await launch(base, pdfUrl, null);
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(String(err)));
  const popups = [];
  try {
    await page.goto(base + '/blank.html', { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => localStorage.clear());

    for (const popupPath of POPUPS) {
      const errorsBefore = pageErrors.length;
      await page.goto(base + popupPath, { waitUntil: 'domcontentloaded' });
      const outcome = await page.evaluate(async (origin) => {
        try {
          CONFIG.API_URL = origin;
          await handleSingleAnalysis();
          const storage = await chrome.storage.local.get(null);
          const stored = Object.entries(storage)
            .filter(([key]) => key.startsWith('analysis_'))
            .map(([key, value]) => ({
              key,
              ok: value && value.ok,
              error: value && value.error,
              nigo_score: value && value.nigo_score,
              source_url: value && value.source_url,
              title: value && (value.pretty_title || value.title),
            }));
          return {
            formTitle: document.getElementById('formTitle') ? document.getElementById('formTitle').textContent : '',
            errorText: document.getElementById('errorText') ? document.getElementById('errorText').textContent : '',
            stored,
          };
        } catch (err) {
          return { thrown: String(err && err.stack || err) };
        }
      }, base);
      outcome.path = popupPath;
      outcome.pageErrors = pageErrors.slice(errorsBefore);
      popups.push(outcome);
    }

    await page.goto(base + '/blank.html', { waitUntil: 'domcontentloaded' });
    const beforeSync = await page.evaluate(() => localStorage.getItem('fg-chrome-storage'));
    await page.addScriptTag({ url: base + '/FormGenome_Extension_Final_Fixed/config.js' });
    await page.addScriptTag({ url: base + '/FormGenome_Extension_Final_Fixed/services/sync.js' });
    const syncDebug = await page.evaluate(async () => {
      const storage = await chrome.storage.local.get(null);
      const entries = Object.entries(storage).map(([key, value]) => ({
        key,
        ok: !!(value && value.ok),
        error: value && value.error,
        synced: !!(value && value._synced),
      }));
      let syncError = null;
      try {
        await syncAnalysisResults();
      } catch (err) {
        syncError = String(err && err.stack || err);
      }
      return { entries, syncError, hasFn: typeof syncAnalysisResults };
    });
    const storageDump = await page.evaluate(() => localStorage.getItem('fg-chrome-storage'));

    const pages = [];
    for (const [dashPath, kind] of PERSONAL_DASHBOARDS) {
      const errorsBefore = pageErrors.length;
      let found;
      let failed = null;
      try {
        await page.goto(base + dashPath, { waitUntil: 'domcontentloaded' });
        await settlePersonal(page);
        found = await inspect(page);
      } catch (err) {
        failed = String(err);
        try {
          found = await inspect(page);
        } catch (err2) {
          found = { inspectError: String(err2) };
        }
      }
      found.path = dashPath;
      found.kind = kind;
      found.failed = failed;
      found.pageErrors = pageErrors.slice(errorsBefore);
      pages.push(found);
    }

    const report = { popups, pages, syncPayloads, storageDump, blocked, beforeSync, syncDebug };
    fs.writeFileSync(outPath, JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
}

async function runTeam(base, storageDump, teamFormsBody, outPath) {
  const { browser, page, blocked } = await launch(base, 'http://127.0.0.1/unused.pdf', teamFormsBody);
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(String(err)));
  const pages = [];
  try {
    await page.goto(base + '/blank.html', { waitUntil: 'domcontentloaded' });
    await page.evaluate((dump) => {
      localStorage.setItem('fg-chrome-storage', dump);
    }, storageDump);

    for (const dashPath of TEAM_DASHBOARDS) {
      const errorsBefore = pageErrors.length;
      let found;
      let failed = null;
      try {
        await page.goto(base + dashPath, { waitUntil: 'domcontentloaded' });
        await page.waitForFunction(() => {
          const count = document.getElementById('formCount');
          return count && !/^\s*0\b/.test(count.textContent || '');
        });
        found = await inspect(page);
        found.edit = await page.evaluate(() => {
          const btn = document.querySelector('.edit-btn');
          if (!btn) return { available: false, styledInputs: [] };
          btn.click();
          const styledInputs = [...document.querySelectorAll('input.edit-field')]
            .filter((input) => input.hasAttribute('style'))
            .map((input) => ({
              name: input.getAttribute('name'),
              style: input.getAttribute('style'),
              value: input.getAttribute('value'),
            }));
          return { available: true, styledInputs };
        });
      } catch (err) {
        failed = String(err);
        try {
          found = await inspect(page);
        } catch (err2) {
          found = { inspectError: String(err2) };
        }
      }
      found.path = dashPath;
      found.kind = 'team';
      found.failed = failed;
      found.pageErrors = pageErrors.slice(errorsBefore);
      pages.push(found);
    }
    fs.writeFileSync(outPath, JSON.stringify({ pages, blocked }, null, 2));
  } finally {
    await browser.close();
  }
}

async function main() {
  const phase = process.argv[2];
  const base = arg('--base');
  const out = arg('--out');
  if (phase === 'personal') {
    await runPersonal(base, arg('--pdf'), out);
  } else if (phase === 'team') {
    const dump = fs.readFileSync(arg('--storage'), 'utf8');
    const forms = fs.readFileSync(arg('--forms'), 'utf8');
    await runTeam(base, dump, forms, out);
  } else {
    throw new Error('phase must be personal or team');
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
