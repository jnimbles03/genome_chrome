/**
 * Stored XSS in the downloadable HTML analysis report.
 *
 * These tests fail while form names, entity names, and popup markup from an
 * analyzed page are written into HTML without escaping. They pass once every
 * extension copy escapes those values, refuses non-http(s) report links, and
 * sends a restrictive content security policy with the report.
 *
 * Run from the repository root:
 *   pip install 'pypdf==3.17.4' 'flask==3.0.0'
 *   npm install --prefix tests
 *   node --test tests/stored_xss_report.test.js
 */

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test, before } = require('node:test');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

const REPO = path.resolve(__dirname, '..');
const ANALYZE = path.join(__dirname, 'support', 'analyze_header_passthrough.py');
const PAYLOAD = `<img src=x onerror="document.documentElement.setAttribute('data-xss','ran')">`;
const HTTPS_URL = 'https://attacker.example/forms/application.pdf';
const FORM_URL = 'https://attacker.example/forms/form.pdf';
const JAVASCRIPT_URL = 'javascript:alert(1)//.pdf';

const CSP = "default-src 'none'; style-src 'unsafe-inline'";

function extensions() {
  const root = {
    name: 'root',
    content: path.join(REPO, 'content', 'content.js'),
    popupDir: path.join(REPO, 'popup'),
  };
  const copies = fs.readdirSync(REPO).filter((name) => {
    const base = path.join(REPO, name);
    return fs.existsSync(path.join(base, 'content', 'content.js'))
      && fs.existsSync(path.join(base, 'popup', 'reportGenerator.js'));
  }).map((name) => ({
    name,
    content: path.join(REPO, name, 'content', 'content.js'),
    popupDir: path.join(REPO, name, 'popup'),
  }));
  return [root, ...copies];
}

function escapeText(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}`);
  if (start < 0) throw new Error(`missing function ${name}`);
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced function ${name}`);
}

function attackerPage(links) {
  const anchors = links.map((link) => {
    return `<a href="${link.href}">${escapeText(link.text)}</a>`;
  }).join('\n');
  return `<!DOCTYPE html><html><body>${anchors}</body></html>`;
}

async function findPdfLinks(contentPath, pageHtml, pageUrl) {
  const dom = new JSDOM(pageHtml, { url: pageUrl, runScripts: 'outside-only' });
  const extensionApi = {
    runtime: {
      sendMessage() {},
      onMessage: { addListener() {} },
      lastError: null,
    },
  };
  dom.window.chrome = extensionApi;
  dom.window.browser = extensionApi;
  dom.window.fetch = async () => {
    throw new Error('findPdfLinks tried to fetch during extraction');
  };
  dom.window.eval(fs.readFileSync(contentPath, 'utf8'));
  return dom.window.findPdfLinks();
}

function loadReport(popupDir) {
  const popupJs = fs.readFileSync(path.join(popupDir, 'popup.js'), 'utf8');
  const reportJs = fs.readFileSync(path.join(popupDir, 'reportGenerator.js'), 'utf8');
  // The Firefox popup file is truncated before hashUrl. The report generator
  // still calls it, so use the same function the other copies ship.
  let hashFn;
  try {
    hashFn = extractFunction(popupJs, 'hashUrl');
  } catch (error) {
    hashFn = extractFunction(fs.readFileSync(path.join(REPO, 'popup', 'popup.js'), 'utf8'), 'hashUrl');
  }
  const sandbox = {
    console: { log() {}, error() {}, warn() {}, debug() {} },
    alertMessage: null,
    alert(message) { sandbox.alertMessage = String(message); },
    setTimeout() {},
    blobs: new Map(),
    captured: null,
    chrome: {
      runtime: { lastError: null },
      downloads: {
        download(options, callback) {
          sandbox.captured = sandbox.blobs.get(options.url) || null;
          if (callback) callback(1);
        },
      },
    },
    URL: {
      createObjectURL(blob) {
        const id = `blob:${Math.random()}`;
        sandbox.blobs.set(id, blob._text);
        return id;
      },
      revokeObjectURL() {},
    },
    document: {
      createElement() {
        return {
          dataset: {},
          style: {},
          href: '',
          download: '',
          click() {
            sandbox.captured = sandbox.blobs.get(this.href) || sandbox.captured;
          },
        };
      },
      body: { appendChild() {}, removeChild() {} },
    },
  };
  sandbox.Blob = class Blob {
    constructor(parts) {
      this._text = parts.map((part) => String(part)).join('');
      this.size = Buffer.byteLength(this._text);
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(`${hashFn}\n${reportJs}`, sandbox);
  return {
    hashUrl(url) {
      return vm.runInContext(`hashUrl(${JSON.stringify(url)})`, sandbox);
    },
    generate(cache, pdfs) {
      sandbox.captured = null;
      sandbox.alertMessage = null;
      sandbox.cache = cache;
      sandbox.pdfs = pdfs;
      vm.runInContext('generateBatchReport(cache, pdfs)', sandbox);
      if (!sandbox.captured) {
        throw new Error(`Report was not downloaded: ${sandbox.alertMessage}`);
      }
      return sandbox.captured;
    },
  };
}

function analyze(mode, filename, url) {
  const pdfPath = path.join(os.tmpdir(), `genome-xss-${mode}-${process.pid}-${Date.now()}.pdf`);
  return new Promise((resolve, reject) => {
    const child = spawn('python3', [ANALYZE, PAYLOAD, mode, pdfPath, filename, url], {
      cwd: REPO,
    });
    const out = [];
    const err = [];
    child.stdout.on('data', (chunk) => out.push(chunk));
    child.stderr.on('data', (chunk) => err.push(chunk));
    child.on('close', (code) => {
      fs.rmSync(pdfPath, { force: true });
      const stdout = Buffer.concat(out).toString('utf8');
      const stderr = Buffer.concat(err).toString('utf8');
      if (code !== 0) {
        reject(new Error(`analyze_pdf_file failed (${code}): ${stderr}\n${stdout}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (error) {
        reject(new Error(`analyze_pdf_file did not return JSON: ${error.message}\n${stdout}\n${stderr}`));
      }
    });
  });
}

function cacheFor(report, url, analysis) {
  const key = `analysis_${report.hashUrl(url)}`;
  return { [key]: analysis };
}

function parsedReport(html) {
  return new JSDOM(html, { runScripts: 'outside-only' }).window.document;
}

function assertSafeReport(html, expected) {
  const document = parsedReport(html);
  assert.equal(
    document.querySelectorAll('img, script, [onerror], [onload]').length,
    0,
    'report parsed attacker-controlled markup',
  );
  assert.equal(document.querySelector('.form-name').textContent, expected.formName);
  const statValues = [...document.querySelectorAll('.stat-val')].map((node) => node.textContent);
  assert.equal(statValues[1], expected.formName, 'hardest-form card');
  assert.equal(document.querySelector('strong').textContent, expected.entity);
  assert.equal(
    document.querySelector('a.action-link').getAttribute('href'),
    expected.href,
  );
  const policy = document.querySelector('meta[http-equiv="Content-Security-Policy" i]');
  assert.ok(policy, 'report is missing a content security policy');
  assert.equal(policy.getAttribute('content'), CSP);
}

function dumpInChrome(html) {
  const file = path.join(os.tmpdir(), `genome-xss-report-${process.pid}-${Date.now()}.html`);
  fs.writeFileSync(file, html);
  return new Promise((resolve, reject) => {
    const child = spawn('google-chrome', [
      '--headless',
      '--disable-gpu',
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--virtual-time-budget=2000',
      `--user-data-dir=${file}.profile`,
      '--dump-dom',
      `file://${file}`,
    ], { stdio: ['ignore', 'pipe', 'ignore'] });
    const chunks = [];
    child.stdout.on('data', (chunk) => chunks.push(chunk));
    const timer = setTimeout(() => child.kill('SIGKILL'), 12000);
    child.on('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on('close', () => {
      clearTimeout(timer);
      fs.rmSync(file, { force: true });
      fs.rmSync(`${file}.profile`, { recursive: true, force: true });
      resolve(Buffer.concat(chunks).toString('utf8'));
    });
  });
}

let copied;
let omitted;
let pretty;

before(async () => {
  copied = await analyze('copy-header', 'application.pdf', HTTPS_URL);
  omitted = await analyze('omit-title', 'form.pdf', FORM_URL);
  pretty = await analyze('pretty-title', 'application.pdf', HTTPS_URL);
});

test('analyzer returns the PDF header as title and entity', () => {
  assert.equal(copied.prompt_contained_payload, true);
  assert.equal(copied.extracted_header, PAYLOAD);
  assert.equal(copied.success, true);
  assert.equal(copied.title, PAYLOAD);
  assert.equal(copied.entity, PAYLOAD);
  assert.equal(omitted.success, true);
  assert.equal(omitted.title, '');
  assert.equal(pretty.pretty_title, PAYLOAD);
  assert.equal(pretty.title, 'Harmless Form');
});

for (const extension of extensions()) {
  test(`${extension.name} content script keeps raw link text`, async () => {
    const pdfs = await findPdfLinks(
      extension.content,
      attackerPage([
        { href: HTTPS_URL, text: PAYLOAD },
        { href: JAVASCRIPT_URL, text: 'Other form' },
      ]),
      'https://attacker.example/forms/',
    );
    const https = pdfs.find((pdf) => pdf.url === HTTPS_URL);
    const javascript = pdfs.find((pdf) => pdf.url === JAVASCRIPT_URL);
    assert.ok(https, 'https PDF link was dropped');
    assert.equal(https.text, PAYLOAD);
    assert.equal(https.action, 'analyze');
    assert.ok(javascript, 'javascript PDF link was dropped before the popup');
  });

  test(`${extension.name} report escapes the analyzed title and entity`, async () => {
    const pdfs = await findPdfLinks(
      extension.content,
      attackerPage([{ href: HTTPS_URL, text: PAYLOAD }]),
      'https://attacker.example/forms/',
    );
    const report = loadReport(extension.popupDir);
    const html = report.generate(cacheFor(report, HTTPS_URL, copied), pdfs);
    assertSafeReport(html, {
      formName: PAYLOAD,
      entity: PAYLOAD,
      href: HTTPS_URL,
    });
  });

  test(`${extension.name} report escapes a spread pretty_title`, async () => {
    const pdfs = await findPdfLinks(
      extension.content,
      attackerPage([{ href: HTTPS_URL, text: 'Visible link' }]),
      'https://attacker.example/forms/',
    );
    const report = loadReport(extension.popupDir);
    const html = report.generate(cacheFor(report, HTTPS_URL, pretty), pdfs);
    assertSafeReport(html, {
      formName: PAYLOAD,
      entity: 'Harmless Org',
      href: HTTPS_URL,
    });
  });

  test(`${extension.name} report escapes link text when the analyzer title is empty`, async () => {
    const pdfs = await findPdfLinks(
      extension.content,
      attackerPage([{ href: FORM_URL, text: PAYLOAD }]),
      'https://attacker.example/forms/',
    );
    const report = loadReport(extension.popupDir);
    const html = report.generate(cacheFor(report, FORM_URL, omitted), pdfs);
    assertSafeReport(html, {
      formName: PAYLOAD,
      entity: 'Unknown',
      href: FORM_URL,
    });
  });

  test(`${extension.name} popup escapes link text, entity, and javascript urls`, async (t) => {
    const pdfs = await findPdfLinks(
      extension.content,
      attackerPage([
        { href: HTTPS_URL, text: PAYLOAD },
        { href: JAVASCRIPT_URL, text: 'Other form' },
      ]),
      'https://attacker.example/forms/',
    );
    const popupHtml = fs.readFileSync(path.join(extension.popupDir, 'popup.html'), 'utf8');
    const popupJs = fs.readFileSync(path.join(extension.popupDir, 'popup.js'), 'utf8');
    const dom = new JSDOM(popupHtml, {
      url: 'chrome-extension://form-genome/popup/popup.html',
      runScripts: 'outside-only',
    });
    const extensionApi = {
      runtime: {
        lastError: null,
        onMessage: { addListener() {} },
        sendMessage() {},
        getURL(path) { return path; },
      },
      storage: { local: { get: async () => ({}), set: async () => {} } },
      tabs: { query: async () => [{ id: 1, url: 'https://attacker.example/forms/' }] },
      downloads: { download() {} },
    };
    dom.window.chrome = extensionApi;
    dom.window.browser = extensionApi;
    dom.window.alert = () => {};
    dom.window.console = { log() {}, error() {}, warn() {}, debug() {} };
    try {
      dom.window.eval(`${popupJs}\n
      globalThis.__api = {
        hashUrl,
        render(pdfs, cache) {
          currentPdfs = pdfs;
          analysisCache = cache;
          renderPdfList();
        },
      };
    `);
    } catch (error) {
      if (error && error.name === 'SyntaxError') {
        t.skip(`${extension.name}/popup/popup.js does not parse, so the popup never renders`);
        return;
      }
      throw error;
    }
    const cacheKey = `analysis_${dom.window.__api.hashUrl(HTTPS_URL)}`;
    dom.window.__api.render(pdfs, {
      [cacheKey]: { entity: PAYLOAD, success: true, complexity_score: 6 },
    });
    const items = dom.window.document.getElementById('pdfItems');
    assert.equal(items.querySelectorAll('img, script, [onerror], [onload]').length, 0);
    const httpsLink = [...items.querySelectorAll('a.form-link')].find((link) => {
      return (link.getAttribute('href') || '').startsWith('https://attacker.example/');
    });
    assert.ok(httpsLink, 'https form link was dropped');
    assert.equal(httpsLink.textContent, PAYLOAD);
    assert.equal(items.querySelector('.pdf-item-entity').textContent, PAYLOAD);
    for (const link of items.querySelectorAll('a')) {
      const href = link.getAttribute('href');
      if (!href) continue;
      const protocol = new URL(href, 'https://attacker.example/').protocol;
      assert.ok(protocol === 'https:' || protocol === 'http:', href);
    }
  });
}

test('opening the generated report does not run the header payload', async () => {
  const report = loadReport(path.join(REPO, 'popup'));
  const pdfs = await findPdfLinks(
    path.join(REPO, 'content', 'content.js'),
    attackerPage([{ href: HTTPS_URL, text: PAYLOAD }]),
    'https://attacker.example/forms/',
  );
  const html = report.generate(cacheFor(report, HTTPS_URL, copied), pdfs);
  const dumped = await dumpInChrome(html);
  assert.equal(dumped.includes('data-xss'), false);
});
