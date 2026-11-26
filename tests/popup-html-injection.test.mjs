// Spec for the popup PDF-list HTML injection.
// These tests fail while link text, link URLs, and cached entity names are
// written into the popup with innerHTML. They pass once that data is inserted
// as text and non-http(s) URLs are not used as hrefs.
//
// Run:
//   npm install --prefix /tmp/fg-test puppeteer-core@24.15.0
//   ln -sfn /tmp/fg-test/node_modules node_modules
//   node --test --test-concurrency=1 tests/popup-html-injection.test.mjs

import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { once } from 'node:events';
import { test, before, after } from 'node:test';
import puppeteer from 'puppeteer-core';

const REPO = path.resolve(import.meta.dirname, '..');
const CHROME = process.env.CHROME_PATH || '/usr/local/bin/google-chrome';
const BENIGN_TEXT = 'Employee benefits form';

let server;
let baseUrl;
let browser;

function markup() {
  return {
    iframe: `<iframe src=${baseUrl}/attacker/phish.html></iframe>`,
    form: `<form action=${baseUrl}/attacker/collect><input name=secret></form>`,
    img: `"><img src=${baseUrl}/attacker/pixel.gif alt=tracked>`,
    meta: `<meta http-equiv=refresh content="0;url=${baseUrl}/attacker/away">`,
    keyword: `<iframe src=${baseUrl}/attacker/via-keyword.html></iframe> application`,
    entity: `<img src=${baseUrl}/attacker/entity.gif alt=entity-mark>`,
    reportEntity: `<img src=${baseUrl}/attacker/report-entity.gif alt=report-entity>`,
  };
}

function contentType(filePath) {
  if (filePath.endsWith('.js')) return 'text/javascript; charset=utf-8';
  if (filePath.endsWith('.html')) return 'text/html; charset=utf-8';
  if (filePath.endsWith('.css')) return 'text/css; charset=utf-8';
  return 'application/octet-stream';
}

function escapeText(text) {
  return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function attackerHtml(links) {
  // The markup is the link's text, which is what findPdfLinks stores.
  // Entities are decoded by the HTML parser into that text node.
  const body = links.map((link) => {
    if (link.rawHref) {
      return `<a id="${link.id}" href="${link.rawHref}">${escapeText(link.text)}</a>`;
    }
    return `<a id="${link.id}" href="${link.href}">${escapeText(link.text)}</a>`;
  }).join('\n');
  return `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Library</title></head><body>
${body}
</body></html>`;
}

before(async () => {
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/attacker/')) {
      res.writeHead(204);
      res.end();
      return;
    }
    if (url.pathname === '/library') {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      res.end(attackerHtml(JSON.parse(url.searchParams.get('links'))));
      return;
    }
    const filePath = path.normalize(path.join(REPO, decodeURIComponent(url.pathname)));
    if (!filePath.startsWith(REPO) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.writeHead(404);
      res.end('not found');
      return;
    }
    res.writeHead(200, { 'content-type': contentType(filePath) });
    fs.createReadStream(filePath).pipe(res);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  baseUrl = `http://127.0.0.1:${server.address().port}`;

  browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking'],
  });
});

after(async () => {
  await browser?.close();
  server?.close();
});

function installContentChrome() {
  const listeners = [];
  window.__fgListeners = listeners;
  window.fetch = async () => ({
    ok: false,
    type: 'basic',
    status: 404,
    headers: { get() { return null; } },
  });
  window.chrome = {
    runtime: {
      lastError: null,
      sendMessage(_message, callback) {
        if (typeof callback === 'function') callback();
      },
      onMessage: {
        addListener(fn) {
          listeners.push(fn);
        },
      },
    },
  };
}

async function openAttackerPage(links) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.evaluateOnNewDocument(installContentChrome);
  const library = new URL('/library', baseUrl);
  library.searchParams.set('links', JSON.stringify(links));
  await page.goto(library.href, { waitUntil: 'domcontentloaded' });
  await page.addScriptTag({ path: path.join(REPO, 'content', 'content.js') });
  const ready = await page.evaluate(() => window.__fgListeners && window.__fgListeners.length > 0);
  assert.equal(ready, true, `content script did not register a listener: ${errors.join('\n')}`);
  page.__errors = errors;
  return page;
}

async function scan(attacker) {
  return attacker.evaluate(() => new Promise((resolve, reject) => {
    const listener = window.__fgListeners[0];
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) reject(new Error('SCAN_FOR_PDFS did not answer'));
    }, 3000);
    const keepOpen = listener({ type: 'SCAN_FOR_PDFS' }, { tab: { id: 1 } }, (response) => {
      settled = true;
      clearTimeout(timer);
      resolve(structuredClone(response));
    });
    if (keepOpen !== true && !settled) {
      clearTimeout(timer);
      reject(new Error('content script listener did not keep the message channel open'));
    }
  }));
}

async function openPopup(attacker, storage = {}, options = {}) {
  const popup = await browser.newPage();
  const errors = [];
  const subresources = [];
  popup.on('pageerror', (error) => errors.push(String(error)));
  popup.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  popup.on('request', (request) => {
    if (request.url().includes('127.0.0.1:9')) subresources.push(request.url());
  });
  await popup.exposeFunction('__extScan', async () => scan(attacker));
  await popup.evaluateOnNewDocument((initialStorage) => {
    const store = { ...initialStorage };
    window.chrome = {
      runtime: { lastError: null, sendMessage() {}, onMessage: { addListener() {} } },
      tabs: {
        async query() {
          return [{ id: 1, active: true, currentWindow: true }];
        },
        async sendMessage(_tabId, message) {
          if (!message || message.type !== 'SCAN_FOR_PDFS') {
            throw new Error('unexpected extension message');
          }
          return await window.__extScan();
        },
      },
      storage: {
        local: {
          async get(key) {
            if (key === null) return structuredClone(store);
            if (typeof key === 'string') return { [key]: store[key] };
            return {};
          },
          async set(values) {
            Object.assign(store, values);
          },
        },
      },
    };
  }, storage);
  await popup.goto(`${baseUrl}/popup/popup.html`, { waitUntil: 'domcontentloaded' });
  await Promise.race([
    popup.waitForSelector('#pdfItems .pdf-item', { timeout: 8000 }),
    popup.waitForFunction(
      () => !location.pathname.startsWith('/popup/popup.html'),
      { timeout: 8000 },
    ),
  ]).catch(() => {});
  if (options.allowLeave) {
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  const stayed = popup.url().includes('/popup/popup.html');
  if (!stayed && !options.allowLeave) {
    throw new Error(`popup navigated to ${popup.url()} errors=${errors.join(' | ')}`);
  }
  if (stayed) {
    const rendered = await popup.$('#pdfItems .pdf-item');
    if (!rendered && !options.allowLeave) {
      const html = await popup.content();
      throw new Error(`popup did not render a PDF list: ${errors.join('\n')}\n${html.slice(0, 1500)}`);
    }
  }
  popup.__errors = errors;
  popup.__subresources = subresources;
  return popup;
}

const benignLink = { id: 'benign', href: '/files/plain.pdf', text: BENIGN_TEXT };

test('a plain PDF link is listed with its text and http URL', async () => {
  const attacker = await openAttackerPage([benignLink]);
  const popup = await openPopup(attacker);
  const shown = await popup.evaluate((suffix, expected) => {
    const input = document.querySelector(`#pdfItems input[data-url$="${suffix}"]`);
    const item = input && input.closest('.pdf-item');
    const anchor = item && item.querySelector('a.form-link');
    return {
      text: anchor ? anchor.textContent : null,
      href: anchor ? anchor.getAttribute('href') : null,
      dataUrl: input ? input.getAttribute('data-url') : null,
    };
  }, '/files/plain.pdf', BENIGN_TEXT);
  assert.equal(shown.text, BENIGN_TEXT);
  assert.equal(shown.href, `${baseUrl}/files/plain.pdf`);
  assert.equal(shown.dataUrl, `${baseUrl}/files/plain.pdf`);
  await attacker.close();
  await popup.close();
});

test('PDF link text is shown literally and is not parsed as popup HTML', async () => {
  const payload = markup().iframe;
  const attacker = await openAttackerPage([
    { id: 'iframe', href: '/files/benefits.pdf', text: payload },
  ]);
  const response = await scan(attacker);
  const fromPage = response.pdfs.find((pdf) => pdf.url.endsWith('/files/benefits.pdf'));
  assert.ok(fromPage, `content script did not return the PDF link: ${JSON.stringify(response)}`);
  assert.equal(fromPage.text, payload);

  const popup = await openPopup(attacker);
  const rendered = await popup.evaluate((payload) => {
    const input = document.querySelector('#pdfItems input[data-url$="/files/benefits.pdf"]');
    const item = input && input.closest('.pdf-item');
    const anchor = item && item.querySelector('a.form-link');
    const name = item && item.querySelector('.pdf-item-name');
    return {
      found: Boolean(item),
      iframeCount: item ? item.querySelectorAll('iframe').length : -1,
      iframeSrc: item ? item.querySelector('iframe')?.getAttribute('src') || null : null,
      anchorText: anchor ? anchor.textContent : null,
      title: name ? name.getAttribute('title') : null,
      html: item ? item.innerHTML : document.getElementById('pdfItems').innerHTML,
    };
  }, payload);

  assert.equal(rendered.found, true, rendered.html);
  assert.equal(rendered.iframeCount, 0, `popup parsed link text into an iframe: ${rendered.html}`);
  assert.equal(rendered.iframeSrc, null);
  assert.equal(rendered.anchorText, payload, rendered.html);
  assert.equal(rendered.title, payload, rendered.html);
  assert.deepEqual(popup.__subresources.filter((url) => url.includes('/attacker/phish.html')), []);
  await attacker.close();
  await popup.close();
});

test('a form kept because its URL contains /forms/ does not inject a form into the popup', async () => {
  const payload = markup().form;
  const attacker = await openAttackerPage([
    { id: 'form', href: '/forms/apply', text: payload },
  ]);
  const response = await scan(attacker);
  const fromPage = response.pdfs.find((pdf) => pdf.url.endsWith('/forms/apply'));
  assert.ok(fromPage, `keyword link was dropped: ${JSON.stringify(response.pdfs.map((pdf) => pdf.url))}`);
  assert.equal(fromPage.text, payload);
  assert.equal(fromPage.is_implicit, true);

  const popup = await openPopup(attacker);
  const rendered = await popup.evaluate(() => {
    const input = document.querySelector('#pdfItems input[data-url$="/forms/apply"]');
    const item = input && input.closest('.pdf-item');
    const anchor = item && item.querySelector('a.form-link');
    return {
      found: Boolean(item),
      forms: item ? item.querySelectorAll('form').length : -1,
      inputs: item ? item.querySelectorAll('form input').length : -1,
      action: item ? item.querySelector('form')?.getAttribute('action') || null : null,
      anchorText: anchor ? anchor.textContent : null,
      html: item ? item.innerHTML : '',
    };
  });
  assert.equal(rendered.found, true, rendered.html);
  assert.equal(rendered.forms, 0, `popup parsed link text into a form: ${rendered.html}`);
  assert.equal(rendered.inputs, 0, rendered.html);
  assert.equal(rendered.action, null);
  assert.equal(rendered.anchorText, payload, rendered.html);
  await attacker.close();
  await popup.close();
});

test('link text that breaks out of the title attribute does not create an image', async () => {
  const payload = markup().img;
  const attacker = await openAttackerPage([
    { id: 'img', href: '/files/pixel.pdf', text: payload },
  ]);
  const popup = await openPopup(attacker);
  const rendered = await popup.evaluate((payload) => {
    const input = document.querySelector('#pdfItems input[data-url$="/files/pixel.pdf"]');
    const item = input && input.closest('.pdf-item');
    const anchor = item && item.querySelector('a.form-link');
    const name = item && item.querySelector('.pdf-item-name');
    return {
      found: Boolean(item),
      images: item ? item.querySelectorAll('img').length : -1,
      imageSrc: item ? item.querySelector('img')?.getAttribute('src') || null : null,
      anchorText: anchor ? anchor.textContent : null,
      title: name ? name.getAttribute('title') : null,
      html: item ? item.innerHTML : document.getElementById('pdfItems').innerHTML,
    };
  }, payload);
  assert.equal(rendered.found, true, rendered.html);
  assert.equal(rendered.images, 0, `popup parsed link text into an image: ${rendered.html}`);
  assert.equal(rendered.imageSrc, null);
  assert.equal(rendered.anchorText, payload, rendered.html);
  assert.equal(rendered.title, payload, rendered.html);
  assert.deepEqual(popup.__subresources.filter((url) => url.includes('/attacker/pixel.gif')), []);
  await attacker.close();
  await popup.close();
});

test('meta refresh markup in link text stays text and does not navigate the popup', async () => {
  const payload = markup().meta;
  const attacker = await openAttackerPage([
    { id: 'meta', href: '/files/refresh.pdf', text: payload },
  ]);
  const popup = await openPopup(attacker, {}, { allowLeave: true });
  assert.equal(popup.url().startsWith(`${baseUrl}/popup/popup.html`), true, `popup navigated to ${popup.url()}`);
  const rendered = await popup.evaluate(() => {
    const input = document.querySelector('#pdfItems input[data-url$="/files/refresh.pdf"]');
    const item = input && input.closest('.pdf-item');
    const anchor = item && item.querySelector('a.form-link');
    return {
      found: Boolean(item),
      metas: item ? item.querySelectorAll('meta').length : -1,
      refresh: item ? item.querySelector('meta')?.getAttribute('content') || null : null,
      anchorText: anchor ? anchor.textContent : null,
      html: item ? item.innerHTML : '',
    };
  });
  assert.equal(rendered.found, true, rendered.html);
  assert.equal(rendered.metas, 0, `popup parsed a meta refresh: ${rendered.html}`);
  assert.equal(rendered.refresh, null);
  assert.equal(rendered.anchorText, payload, rendered.html);
  await attacker.close();
  await popup.close();
});

test('a link kept because its text contains a form keyword does not inject an iframe', async () => {
  const payload = markup().keyword;
  const attacker = await openAttackerPage([
    { id: 'keyword', href: '/download/file', text: payload },
  ]);
  const response = await scan(attacker);
  const fromPage = response.pdfs.find((pdf) => pdf.url.endsWith('/download/file'));
  assert.ok(fromPage, `text keyword link was dropped: ${JSON.stringify(response.pdfs.map((pdf) => [pdf.url, pdf.text]))}`);
  assert.equal(fromPage.text, payload);

  const popup = await openPopup(attacker);
  const rendered = await popup.evaluate((payload) => {
    const input = document.querySelector('#pdfItems input[data-url$="/download/file"]');
    const item = input && input.closest('.pdf-item');
    const anchor = item && item.querySelector('a.form-link');
    return {
      found: Boolean(item),
      iframes: item ? item.querySelectorAll('iframe').length : -1,
      src: item ? item.querySelector('iframe')?.getAttribute('src') || null : null,
      anchorText: anchor ? anchor.textContent : null,
      html: item ? item.innerHTML : '',
    };
  }, payload);
  assert.equal(rendered.found, true, rendered.html);
  assert.equal(rendered.iframes, 0, `popup parsed keyword-link text into an iframe: ${rendered.html}`);
  assert.equal(rendered.src, null);
  assert.equal(rendered.anchorText, payload, rendered.html);
  assert.deepEqual(popup.__subresources.filter((url) => url.includes('/attacker/via-keyword.html')), []);
  await attacker.close();
  await popup.close();
});

test('a quoted PDF URL cannot break out of href or data-url attributes', async () => {
  const attacker = await openAttackerPage([
    {
      id: 'quoted-url',
      href: '/files/q.pdf?x=%22%3E%3Cimg%20class%3Dfrom-url%20src%3Dhttp%3A%2F%2F127.0.0.1%3A9%2Ffrom-url.gif%3E',
      text: 'Quoted URL',
    },
  ]);
  const response = await scan(attacker);
  const fromPage = response.pdfs.find((pdf) => pdf.filename === 'q.pdf' || (pdf.url || '').includes('/files/q.pdf'));
  const popup = await openPopup(attacker);
  const rendered = await popup.evaluate(() => {
    const images = [...document.querySelectorAll('#pdfItems img')].map((img) => img.getAttribute('src'));
    const fromUrl = document.querySelector('#pdfItems img.from-url, #pdfItems img[src*="from-url.gif"]');
    const inputs = [...document.querySelectorAll('#pdfItems input[data-url]')].map((input) => input.getAttribute('data-url'));
    const hrefs = [...document.querySelectorAll('#pdfItems a')].map((anchor) => anchor.getAttribute('href'));
    return {
      images,
      fromUrl: Boolean(fromUrl),
      inputs,
      hrefs,
      html: document.getElementById('pdfItems').innerHTML,
    };
  });
  assert.equal(rendered.fromUrl, false, `URL broke out of an attribute: ${rendered.html}`);
  assert.equal(rendered.images.some((src) => (src || '').includes('from-url.gif')), false, rendered.html);
  for (const value of [...rendered.inputs, ...rendered.hrefs]) {
    assert.equal(value.includes('<'), false, `attribute contains markup: ${value}\n${rendered.html}`);
    assert.equal(value.includes('>'), false, `attribute contains markup: ${value}`);
  }
  if (fromPage) {
    assert.equal(fromPage.url.includes('<'), false, `content script stored a markup URL: ${fromPage.url}`);
  }
  await attacker.close();
  await popup.close();
});

test('popup links do not use javascript or data URLs', async () => {
  const attacker = await openAttackerPage([
    benignLink,
    { id: 'js-url', rawHref: 'javascript:void(0)//.pdf', text: 'javascript link' },
  ]);
  const response = await scan(attacker);
  const popup = await openPopup(attacker);
  const hrefs = await popup.evaluate(() =>
    [...document.querySelectorAll('#pdfItems a, #pdfItems [data-url]')]
      .map((node) => node.getAttribute('href') || node.getAttribute('data-url') || ''));
  assert.ok(response.pdfs.length > 0);
  for (const value of hrefs) {
    const scheme = value.trim().toLowerCase().split(':')[0];
    assert.ok(scheme === 'http' || scheme === 'https', `non-http(s) URL reached the popup: ${value}`);
  }
  await attacker.close();
  await popup.close();
});

test('a cached entity name is shown as text and is not parsed as HTML', async () => {
  const payload = markup().entity;
  const attacker = await openAttackerPage([benignLink]);
  const response = await scan(attacker);
  const pdf = response.pdfs.find((entry) => entry.url.endsWith('/files/plain.pdf'));
  assert.ok(pdf);

  const hasher = await browser.newPage();
  await hasher.addScriptTag({ path: path.join(REPO, 'popup', 'popup.js') });
  const cacheKey = await hasher.evaluate((url) => `analysis_${hashUrl(url)}`, pdf.url);
  await hasher.close();

  const storage = {
    [cacheKey]: {
      entity: payload,
      _cached_at: Date.now(),
      confidence_tier: 'High',
    },
  };
  const popup = await openPopup(attacker, storage);
  const rendered = await popup.evaluate((payload) => {
    const input = document.querySelector('#pdfItems input[data-url$="/files/plain.pdf"]');
    const item = input && input.closest('.pdf-item');
    const entity = item && item.querySelector('.pdf-item-entity');
    return {
      found: Boolean(entity),
      images: entity ? entity.querySelectorAll('img').length : -1,
      src: entity ? entity.querySelector('img')?.getAttribute('src') || null : null,
      text: entity ? entity.textContent : null,
      html: entity ? entity.innerHTML : item ? item.innerHTML : '',
    };
  }, payload);
  assert.equal(rendered.found, true, rendered.html);
  assert.equal(rendered.images, 0, `popup parsed the cached entity into an image: ${rendered.html}`);
  assert.equal(rendered.src, null);
  assert.equal(rendered.text, payload, rendered.html);
  assert.deepEqual(popup.__subresources.filter((url) => url.includes('/attacker/entity.gif')), []);
  await attacker.close();
  await popup.close();
});

test('the downloaded HTML report does not parse PDF link text or entity names', async () => {
  const payload = markup().iframe;
  const entity = markup().reportEntity;
  const attacker = await openAttackerPage([
    { id: 'iframe', href: '/files/benefits.pdf', text: payload },
  ]);
  const response = await scan(attacker);
  const pdf = response.pdfs.find((entry) => entry.url.endsWith('/files/benefits.pdf'));
  assert.equal(pdf.text, payload);
  const popup = await openPopup(attacker);
  const report = await popup.evaluate((form) => {
    const analyzed = {
      ok: true,
      complexity_score: 1,
      pdf_url: form.url,
      pdf_name: form.text,
      language_count: form.language_count,
      entity_name: form.entityName,
    };
    const stats = calculateStats([analyzed]);
    const html = generateReportHTML([analyzed], stats);
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    return {
      iframes: parsed.querySelectorAll('iframe').length,
      images: parsed.querySelectorAll('img').length,
      name: parsed.querySelector('.form-name')?.textContent || null,
      entity: parsed.body.textContent,
      html,
    };
  }, { ...pdf, entityName: entity });
  assert.equal(report.iframes, 0, `report parsed link text into an iframe: ${report.html.slice(0, 500)}`);
  assert.equal(report.images, 0, `report parsed entity name into an image: ${report.html.slice(0, 500)}`);
  assert.equal(report.name, payload, report.html.slice(0, 800));
  assert.ok(report.entity.includes(entity), report.html.slice(0, 800));
  await attacker.close();
  await popup.close();
});
