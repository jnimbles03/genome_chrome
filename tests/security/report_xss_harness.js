#!/usr/bin/env node
/**
 * Opens HTML built by the real report generator (and the popup list) in Chrome.
 *
 * Fails while dynamic report fields are concatenated into markup. Passes once
 * those fields are escaped so the saved file shows the original text and does
 * not run it. A Content-Security-Policy alone does not pass: the text must
 * still be present, and the payload must not become an element.
 *
 * Usage: node tests/security/report_xss_harness.js <fixture-dir>
 * fixture-dir holds *.json pipeline cases written by test_report_stored_xss.py.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');
const vm = require('vm');

const REPO = path.resolve(__dirname, '..', '..');
const FIXTURE_DIR = process.argv[2];

const TITLE_MARK = '<img src=x onerror=document.documentElement.dataset.xssTitle=1>';
const ENTITY_MARK = '</title><img src=x onerror=document.documentElement.dataset.xssEntity=1>';
const PAGE_MARK = '<img src=x onerror=document.documentElement.dataset.xssPage=1>';
const FIELD_MARK = '<img src=x onerror=document.documentElement.dataset.xssField=1>';
const SCORE_MARK = '<img src=x onerror=document.documentElement.dataset.xssScore=1>';
const URL_MARK = 'https://attacker.example/a.pdf"><img src=x onerror=document.documentElement.dataset.xssUrl=1>';
const POPUP_MARK = '<img src=x onerror=document.documentElement.dataset.xssPopup=1>';
const SAFE_URL = 'https://attacker.example/forms/member.pdf';

const GENERATORS = [
  {
    name: 'popup/reportGenerator.js',
    popup: 'popup/popup.js',
    report: 'popup/reportGenerator.js',
  },
  {
    name: 'FormGenome_Extension_Firefox/popup/reportGenerator.js',
    popup: 'popup/popup.js',
    report: 'FormGenome_Extension_Firefox/popup/reportGenerator.js',
  },
];

function chromeBin() {
  for (const bin of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    const found = ['/usr/bin/' + bin, '/usr/local/bin/' + bin].find((p) => fs.existsSync(p));
    if (found) return found;
  }
  return 'google-chrome';
}

function httpGet(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve(data));
    });
    req.on('error', reject);
  });
}

class Chrome {
  constructor() {
    this.port = 9400 + (process.pid % 500);
    this.profile = fs.mkdtempSync(path.join(os.tmpdir(), 'fg-xss-'));
    this.child = null;
    this.ws = null;
    this.nextId = 0;
    this.pending = new Map();
  }

  async start() {
    this.child = spawn(chromeBin(), [
      '--headless=new',
      '--no-sandbox',
      '--disable-gpu',
      '--disable-dev-shm-usage',
      '--no-first-run',
      '--user-data-dir=' + this.profile,
      '--remote-debugging-port=' + this.port,
      'about:blank',
    ], { stdio: 'ignore' });
    let version = null;
    for (let i = 0; i < 50; i++) {
      try {
        version = JSON.parse(await httpGet('http://127.0.0.1:' + this.port + '/json/version'));
        break;
      } catch (err) {
        await new Promise((r) => setTimeout(r, 100));
      }
    }
    if (!version) throw new Error('Chrome did not open a debugging port');
    const wsUrl = version.webSocketDebuggerUrl;
    this.ws = new WebSocket(wsUrl);
    await new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve);
      this.ws.addEventListener('error', () => reject(new Error('Chrome websocket failed')));
    });
    this.ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    });
    const { targetId } = await this.send('Target.createTarget', { url: 'about:blank' });
    const session = await this.send('Target.attachToTarget', { targetId, flatten: true });
    this.sessionId = session.sessionId;
  }

  send(method, params, sessionId) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      const payload = { id, method, params: params || {} };
      if (sessionId || this.sessionId) payload.sessionId = sessionId || this.sessionId;
      this.ws.send(JSON.stringify(payload));
    });
  }

  async inspect(html) {
    const file = path.join(this.profile, 'case-' + this.nextId + '.html');
    fs.writeFileSync(file, html);
    await this.send('Page.enable');
    await this.send('Page.navigate', { url: 'file://' + file });
    await new Promise((r) => setTimeout(r, 400));
    const evaluated = await this.send('Runtime.evaluate', {
      expression: `(() => {
        const dataset = {};
        for (const key of Object.keys(document.documentElement.dataset)) {
          dataset[key] = document.documentElement.dataset[key];
        }
        return JSON.stringify({
          dataset,
          imgs: [...document.images].map((img) => img.outerHTML),
          formNames: [...document.querySelectorAll('.form-name')].map((n) => n.textContent),
          stats: [...document.querySelectorAll('.stat-val')].map((n) => n.textContent),
          entityText: document.querySelector('p.sub strong') ? document.querySelector('p.sub strong').textContent : null,
          docTitle: document.title,
          metas: [...document.querySelectorAll('.form-meta')].map((n) => n.textContent.replace(/\\s+/g, ' ').trim()),
          hrefs: [...document.querySelectorAll('a.action-link')].map((a) => a.getAttribute('href')),
          scores: [...document.querySelectorAll('.complexity-badge')].map((n) => n.textContent.trim()),
          popupEntity: document.querySelector('.pdf-item-entity') ? document.querySelector('.pdf-item-entity').textContent : null,
          popupLink: document.querySelector('a.form-link') ? document.querySelector('a.form-link').textContent : null,
          popupNameTitle: document.querySelector('.pdf-item-name') ? document.querySelector('.pdf-item-name').getAttribute('title') : null,
        });
      })()`,
      returnByValue: true,
    });
    if (!evaluated.result || evaluated.result.value == null) {
      throw new Error('Chrome evaluation failed: ' + JSON.stringify(evaluated));
    }
    return JSON.parse(evaluated.result.value);
  }

  close() {
    if (this.child) this.child.kill('SIGKILL');
  }
}

function createReportContext() {
  const blobMap = new Map();
  const downloads = [];
  const document = {
    body: { appendChild() {}, removeChild() {} },
    createElement(tag) {
      if (tag !== 'a') return { style: {} };
      return {
        href: '',
        download: '',
        dataset: {},
        style: {},
        click() {
          const blob = blobMap.get(this.href);
          downloads.push({
            filename: this.download,
            mime: blob.type,
            content: blob.content,
          });
        },
      };
    },
    addEventListener() {},
    getElementById() {
      return { addEventListener() {}, classList: { add() {}, remove() {} }, textContent: '' };
    },
    querySelectorAll() { return []; },
  };
  class Blob {
    constructor(parts, options) {
      this.content = parts.map((part) => String(part)).join('');
      this.size = this.content.length;
      this.type = options && options.type;
    }
  }
  const context = {
    console,
    document,
    alert(msg) { throw new Error('alert: ' + msg); },
    setTimeout,
    clearTimeout,
    Blob,
    URL: {
      createObjectURL(blob) {
        const id = 'blob:' + Math.random();
        blobMap.set(id, blob);
        return id;
      },
      revokeObjectURL() {},
    },
  };
  context.window = context;
  vm.createContext(context);
  context.__downloads = downloads;
  return context;
}

function loadGenerator(target) {
  const context = createReportContext();
  vm.runInContext(fs.readFileSync(path.join(REPO, target.popup), 'utf8'), context, { filename: target.popup });
  vm.runInContext(fs.readFileSync(path.join(REPO, target.report), 'utf8'), context, { filename: target.report });
  if (typeof context.generateBatchReport !== 'function' || typeof context.hashUrl !== 'function') {
    throw new Error(target.name + ' did not define generateBatchReport and hashUrl');
  }
  return context;
}

function analyzedRecord(extra) {
  return Object.assign({
    success: true,
    complexity_score: 10,
    pages: 2,
    total_field_count: 4,
    title: 'Member Application',
    entity: 'Boeing Employees Credit Union',
    signature_required: false,
    notarization_required: false,
    payment_required: false,
    confidence_tier: 'High',
  }, extra);
}

function buildReport(context, result, pdfs) {
  const cache = {};
  for (const pdf of pdfs) {
    cache['analysis_' + context.hashUrl(pdf.url)] = result.perPdf ? result.perPdf[pdf.url] : result;
  }
  context.generateBatchReport(cache, pdfs);
  const download = context.__downloads[context.__downloads.length - 1];
  if (!download) throw new Error('generateBatchReport did not download a file');
  if (download.filename !== 'Form_Genome_Report_Latest.html') {
    throw new Error('unexpected download name ' + download.filename);
  }
  if (download.mime !== 'text/html') {
    throw new Error('unexpected mime ' + download.mime);
  }
  return download.content;
}

function wrapFragment(fragment) {
  const payload = JSON.stringify(fragment).replace(/</g, '\\u003c');
  return '<!DOCTYPE html><meta charset="utf-8"><div id="host"></div><script>' +
    'document.getElementById("host").innerHTML = ' + payload + ';</script>';
}

function popupListHtml(pdfs, cache) {
  const context = createReportContext();
  // Richer DOM, and the driver runs in the same script as the top-level lets.
  const els = {};
  function makeEl() {
    return {
      innerHTML: '',
      className: '',
      textContent: '',
      id: '',
      checked: false,
      indeterminate: false,
      style: {},
      dataset: {},
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      children: [],
      appendChild(child) { this.children.push(child); return child; },
      querySelector() { return makeEl(); },
      querySelectorAll() { return []; },
      addEventListener() {},
    };
  }
  context.document.getElementById = (id) => {
    if (!els[id]) els[id] = makeEl();
    return els[id];
  };
  context.document.createElement = () => makeEl();
  context.document.querySelectorAll = () => [];
  const source = fs.readFileSync(path.join(REPO, 'popup/popup.js'), 'utf8') +
    '\ncurrentPdfs = ' + JSON.stringify(pdfs) + ';' +
    '\nanalysisCache = ' + JSON.stringify(cache) + ';' +
    '\nrenderPdfList();' +
    '\nglobalThis.__htmls = document.getElementById("pdfItems").children.map((child) => child.innerHTML);';
  vm.runInContext(source, context, { filename: 'popup/popup.js' });
  return context.__htmls;
}

function flagsSet(dom) {
  return Object.keys(dom.dataset).filter((key) => key.toLowerCase().startsWith('xss'));
}

function check(errors, condition, message) {
  if (!condition) errors.push(message);
}

function assertDom(name, dom, expect) {
  const errors = [];
  const fired = flagsSet(dom);
  check(errors, fired.length === 0, 'payload ran and set ' + fired.join(','));
  check(errors, dom.imgs.length === 0, 'payload became an img: ' + dom.imgs.join(' | '));
  if (expect.formNames) {
    check(errors, JSON.stringify(dom.formNames) === JSON.stringify(expect.formNames),
      'form names ' + JSON.stringify(dom.formNames) + ' != ' + JSON.stringify(expect.formNames));
  }
  if (expect.entityText != null) {
    check(errors, dom.entityText === expect.entityText, 'entity text ' + JSON.stringify(dom.entityText));
  }
  if (expect.docTitle != null) {
    check(errors, dom.docTitle === expect.docTitle, 'document title ' + JSON.stringify(dom.docTitle));
  }
  if (expect.statIncludes != null) {
    check(errors, dom.stats.includes(expect.statIncludes), 'stats ' + JSON.stringify(dom.stats));
  }
  if (expect.metas) {
    check(errors, JSON.stringify(dom.metas) === JSON.stringify(expect.metas),
      'metas ' + JSON.stringify(dom.metas));
  }
  if (expect.scores) {
    check(errors, JSON.stringify(dom.scores) === JSON.stringify(expect.scores),
      'scores ' + JSON.stringify(dom.scores));
  }
  if (expect.hrefs) {
    check(errors, JSON.stringify(dom.hrefs) === JSON.stringify(expect.hrefs),
      'hrefs ' + JSON.stringify(dom.hrefs));
  }
  if (expect.popupEntity != null) {
    check(errors, dom.popupEntity === expect.popupEntity, 'popup entity ' + JSON.stringify(dom.popupEntity));
  }
  if (expect.popupLink != null) {
    check(errors, dom.popupLink === expect.popupLink, 'popup link text ' + JSON.stringify(dom.popupLink));
  }
  if (expect.popupNameTitle != null) {
    check(errors, dom.popupNameTitle === expect.popupNameTitle,
      'popup title attribute ' + JSON.stringify(dom.popupNameTitle));
  }
  if (errors.length) {
    return name + ' FAIL\n  ' + errors.join('\n  ');
  }
  return name + ' PASS';
}

function syntheticCases() {
  const pdf = { url: SAFE_URL, text: 'Member form', language_count: 1 };
  return [
    {
      name: 'benign report still renders names',
      result: analyzedRecord({}),
      pdfs: [pdf],
      expect: {
        formNames: ['Member Application'],
        entityText: 'Boeing Employees Credit Union',
        docTitle: 'Form Analysis Report — Boeing Employees Credit Union',
        statIncludes: 'Member Application',
        hrefs: [SAFE_URL],
        metas: ['2 pages • 4 fields'],
      },
    },
    {
      name: 'pretty_title is escaped in the table and the hardest-form card',
      result: analyzedRecord({ pretty_title: TITLE_MARK, complexity_score: 80 }),
      pdfs: [pdf],
      expect: {
        formNames: [TITLE_MARK],
        statIncludes: TITLE_MARK,
        entityText: 'Boeing Employees Credit Union',
      },
    },
    {
      name: 'form_title is escaped when pretty_title is absent',
      result: analyzedRecord({ form_title: TITLE_MARK, title: 'Safe Title' }),
      pdfs: [pdf],
      expect: { formNames: [TITLE_MARK], statIncludes: TITLE_MARK },
    },
    {
      name: 'form_name is escaped when earlier title fields are absent',
      result: analyzedRecord({ form_name: TITLE_MARK, title: 'Safe Title' }),
      pdfs: [pdf],
      expect: { formNames: [TITLE_MARK] },
    },
    {
      name: 'title is escaped',
      result: analyzedRecord({ title: TITLE_MARK }),
      pdfs: [pdf],
      expect: { formNames: [TITLE_MARK], statIncludes: TITLE_MARK },
    },
    {
      name: 'pdf link text is escaped when the API title is blank',
      result: analyzedRecord({ title: '   ', pretty_title: '', form_title: '', form_name: '' }),
      pdfs: [{ url: SAFE_URL, text: TITLE_MARK, language_count: 1 }],
      expect: { formNames: [TITLE_MARK], statIncludes: TITLE_MARK },
    },
    {
      name: 'entity_name is escaped in the title and the intro',
      result: analyzedRecord({ entity_name: ENTITY_MARK, entity: 'Safe Entity' }),
      pdfs: [pdf],
      expect: {
        entityText: ENTITY_MARK,
        docTitle: 'Form Analysis Report — ' + ENTITY_MARK,
      },
    },
    {
      name: 'entity is escaped when entity_name is absent',
      result: analyzedRecord({ entity: ENTITY_MARK }),
      pdfs: [pdf],
      expect: {
        entityText: ENTITY_MARK,
        docTitle: 'Form Analysis Report — ' + ENTITY_MARK,
      },
    },
    {
      name: 'a second form name is escaped even when it is not the hardest',
      result: null,
      perPdf: {
        [SAFE_URL]: analyzedRecord({ title: 'Easy Form', complexity_score: 10 }),
        'https://attacker.example/forms/hard.pdf': analyzedRecord({ title: 'Hard Form', complexity_score: 90 }),
      },
      // The payload is on the lower-scoring form so the table sink is distinct from the hardest card.
      pdfs: [
        { url: SAFE_URL, text: 'Easy', language_count: 1, resultTitle: TITLE_MARK },
        { url: 'https://attacker.example/forms/hard.pdf', text: 'Hard', language_count: 1 },
      ],
      expect: {
        formNames: [TITLE_MARK, 'Hard Form'],
        statIncludes: 'Hard Form',
      },
      prepare(pdfs) {
        this.perPdf[SAFE_URL].title = TITLE_MARK;
        this.perPdf[SAFE_URL].complexity_score = 10;
      },
    },
    {
      name: 'page count is escaped',
      result: analyzedRecord({ pages: PAGE_MARK, total_field_count: 4 }),
      pdfs: [pdf],
      expect: { metas: [PAGE_MARK + ' pages • 4 fields'] },
    },
    {
      name: 'total_field_count is escaped',
      result: analyzedRecord({ pages: 2, total_field_count: FIELD_MARK }),
      pdfs: [pdf],
      expect: { metas: ['2 pages • ' + FIELD_MARK + ' fields'] },
    },
    {
      name: 'field_count is escaped when total_field_count is absent',
      result: analyzedRecord({ pages: 2, total_field_count: 0, field_count: FIELD_MARK }),
      pdfs: [pdf],
      expect: { metas: ['2 pages • ' + FIELD_MARK + ' fields'] },
    },
    {
      name: 'complexity score is escaped',
      result: analyzedRecord({ complexity_score: SCORE_MARK }),
      pdfs: [pdf],
      expect: { scores: [SCORE_MARK] },
    },
    {
      name: 'pdf url cannot break out of the href',
      result: analyzedRecord({}),
      pdfs: [{ url: URL_MARK, text: 'Member form', language_count: 1 }],
      expect: { hrefs: [URL_MARK] },
    },
  ];
}

function pipelineCases() {
  if (!FIXTURE_DIR) return [];
  return fs.readdirSync(FIXTURE_DIR).filter((name) => name.endsWith('.json')).sort().map((name) => {
    const fixture = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, name), 'utf8'));
    return {
      name: 'pipeline ' + fixture.name,
      result: fixture.result,
      pdfs: [{
        url: fixture.pdfUrl,
        text: fixture.pdfText,
        language_count: fixture.languageCount || 1,
      }],
      expect: {
        formNames: [fixture.expectTitle],
        statIncludes: fixture.expectTitle,
        entityText: fixture.expectEntity,
        docTitle: 'Form Analysis Report — ' + fixture.expectEntity,
        hrefs: [fixture.pdfUrl],
      },
    };
  });
}

function popupCases() {
  const url = SAFE_URL;
  const hashSource = loadGenerator(GENERATORS[0]);
  const key = 'analysis_' + hashSource.hashUrl(url);
  return [
    {
      name: 'popup list escapes entity, link text, and the title attribute',
      htmls() {
        const cache = {};
        cache[key] = analyzedRecord({
          entity: POPUP_MARK,
          confidence_tier: 'High',
          _cached_at: Date.now(),
        });
        return popupListHtml([{
          url,
          text: POPUP_MARK,
          language_count: 1,
          action: 'analyze',
        }], cache);
      },
      expect: {
        popupEntity: POPUP_MARK,
        popupLink: POPUP_MARK,
        popupNameTitle: POPUP_MARK,
      },
    },
  ];
}

async function main() {
  const chrome = new Chrome();
  const failures = [];
  try {
    await chrome.start();
    const cases = syntheticCases().concat(pipelineCases());
    for (const target of GENERATORS) {
      const context = loadGenerator(target);
      for (const spec of cases) {
        if (typeof spec.prepare === 'function') spec.prepare(spec.pdfs);
        let html;
        try {
          html = buildReport(context, spec.perPdf ? { perPdf: spec.perPdf } : spec.result, spec.pdfs);
        } catch (err) {
          failures.push(target.name + ' ' + spec.name + ' FAIL\n  ' + err.stack);
          continue;
        }
        const dom = await chrome.inspect(html);
        const line = assertDom(target.name + ' :: ' + spec.name, dom, spec.expect);
        console.log(line);
        if (line.includes(' FAIL')) failures.push(line);
      }
    }
    for (const spec of popupCases()) {
      const fragments = spec.htmls();
      const html = '<!DOCTYPE html><meta charset="utf-8">' + fragments.join('');
      const dom = await chrome.inspect(html);
      const line = assertDom(spec.name, dom, spec.expect);
      console.log(line);
      if (line.includes(' FAIL')) failures.push(line);
    }
  } finally {
    chrome.close();
  }
  if (failures.length) {
    console.error('\n' + failures.length + ' case(s) failed');
    process.exit(1);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
