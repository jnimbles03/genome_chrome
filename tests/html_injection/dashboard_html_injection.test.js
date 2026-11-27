/**
 * Spec for stored-analysis HTML injection in the shared extension builds.
 *
 * These tests fail while analysis strings are concatenated into HTML.
 * They pass when every rendered cell is text, and a source_url link is
 * created only for a URL whose whole value is http: or https:.
 *
 * Not covered here: team-dashboard.js still interpolates action_type,
 * payment_amount, estimated_signer_time, and estimated_processing_time
 * without escaping, but those cells are filled from the team API and
 * Postgres, which this run does not start. Popup markup built from the
 * scanned page's link text and URL (pdf.text, pdf.url) is a separate
 * input from the stored analysis and is not asserted.
 *
 * Run from the repository root:
 *   python3 -m pip install --user pypdf==3.17.4 Flask==3.0.0 python-dotenv==1.0.0 'psycopg2-binary>=2.9.9'
 *   npm install --prefix tests/html_injection
 *   node tests/html_injection/dashboard_html_injection.test.js
 */

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.resolve(__dirname, "../..");
const PAYLOADS = JSON.parse(fs.readFileSync(path.join(__dirname, "payloads.json"), "utf8"));
const BASE = "http://127.0.0.1:8080";
const PDFS = [
  { id: "breakout", url: `${BASE}/fixtures/breakout.pdf`, text: "Membership packet" },
  { id: "https", url: `${BASE}/fixtures/https-link.pdf`, text: "Membership packet" },
  { id: "javascript", url: `${BASE}/fixtures/javascript-link.pdf`, text: "Membership packet" },
];

const TEXT_COLUMNS = {
  form_name: "TITLE",
  entity_name: "ENTITY_NAME",
  action_type: "ACTION",
  confidence_tier: "TIER",
  payment_amount: "PAYMENT",
  form_purpose: "PURPOSE",
  industry_vertical: "VERTICAL",
  industry_subvertical: "SUB",
  estimated_signer_time: "SIGNER",
  estimated_processing_time: "PROC",
};

const DASHBOARDS = [
  {
    name: "extracted_chrome_share",
    html: "extracted_chrome_share/dashboard/dashboard.html",
    scripts: [
      "extracted_chrome_share/dashboard/dashboard.js",
    ],
    api: "chrome",
  },
  {
    name: "FormGenome_Extension_Clean",
    html: "FormGenome_Extension_Clean/dashboard/dashboard.html",
    scripts: [
      "FormGenome_Extension_Clean/dashboard/dashboard.js",
    ],
    api: "chrome",
  },
  {
    name: "FormGenome_Extension_Firefox",
    html: "FormGenome_Extension_Firefox/dashboard/dashboard.html",
    scripts: [
      "FormGenome_Extension_Firefox/dashboard/dashboard.js",
    ],
    api: "browser",
  },
];

const POPUPS = [
  "extracted_chrome_share",
  "FormGenome_Extension_Clean",
];

const REPORTS = [
  "extracted_chrome_share",
  "FormGenome_Extension_Clean",
  "FormGenome_Extension_Firefox",
];

function assert(condition, message) {
  if (!condition) {
    const error = new Error(message);
    error.isAssertion = true;
    throw error;
  }
}

function memoryStorage(initial) {
  const data = { ...initial };
  return {
    local: {
      async get(keys) {
        if (keys == null) return { ...data };
        if (typeof keys === "string") return { [keys]: data[keys] };
        const out = {};
        for (const key of keys) out[key] = data[key];
        return out;
      },
      async set(items) {
        Object.assign(data, items);
      },
      async clear() {
        for (const key of Object.keys(data)) delete data[key];
      },
    },
    snapshot() {
      return JSON.parse(JSON.stringify(data));
    },
  };
}

function extensionApi(storage, namespace) {
  const tabs = {
    url: PDFS[0].url,
    async query() {
      return [{ id: 1, url: tabs.url }];
    },
    async sendMessage() {
      return {
        pdfs: PDFS.map((pdf) => ({ url: pdf.url, text: pdf.text, action: "download" })),
      };
    },
    create() {},
  };
  const api = {
    storage,
    tabs,
    runtime: {
      getURL: (p) => p,
      async sendMessage() {
        return { success: false, error: "fixture is a pdf, not a landing page" };
      },
    },
  };
  return { api, tabs, namespace };
}

function inlineHtml(htmlPath, scriptPaths) {
  let html = fs.readFileSync(path.join(ROOT, htmlPath), "utf8");
  html = html.replace(/<script\s+src="[^"]+"><\/script>/g, "");
  const blocks = scriptPaths
    .map((scriptPath) => `<script>\n${fs.readFileSync(path.join(ROOT, scriptPath), "utf8")}\n</script>`)
    .join("\n");
  return html.replace("</body>", `${blocks}\n</body>`);
}

function installNetwork(window) {
  // The page's fetch, FormData, and Blob are the Node implementations, so the
  // popup's download and multipart upload are real HTTP requests to the local
  // product server. They are not a second copy of the analyzer.
  window.fetch = globalThis.fetch.bind(globalThis);
  window.FormData = globalThis.FormData;
  window.Blob = globalThis.Blob;
  window.File = globalThis.File;
  window.Headers = globalThis.Headers;
  window.Response = globalThis.Response;
  window.Request = globalThis.Request;
}

async function loadPage(htmlPath, scriptPaths, storage, namespace) {
  const virtualConsole = new VirtualConsole();
  const logs = [];
  virtualConsole.on("error", (message) => logs.push(String(message)));
  virtualConsole.on("jsdomError", (error) => logs.push(String(error)));
  const { api } = extensionApi(storage, namespace);
  const dom = new JSDOM(inlineHtml(htmlPath, scriptPaths), {
    runScripts: "dangerously",
    url: "chrome-extension://formgenome/popup/popup.html",
    virtualConsole,
    beforeParse(window) {
      window[namespace] = api;
      if (namespace === "browser") window.chrome = api;
      window.alert = () => {};
      window.confirm = () => false;
      installNetwork(window);
    },
  });
  dom.logs = logs;
  return dom;
}

async function waitFor(label, predicate) {
  const started = Date.now();
  let last = "";
  while (Date.now() - started < 20000) {
    try {
      const value = await predicate();
      if (value) return value;
    } catch (error) {
      last = error.message;
    }
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  throw new Error(`timed out waiting for ${label}${last ? `: ${last}` : ""}`);
}

async function analyzeThroughPopup(rootName) {
  const storage = memoryStorage({ apiPreference: "local" });
  const dom = await loadPage(
    `${rootName}/popup/popup.html`,
    [`${rootName}/config.js`, `${rootName}/popup/popup.js`, `${rootName}/popup/reportGenerator.js`],
    storage,
    "chrome",
  );
  const { window } = dom;
  await waitFor(`${rootName} popup list`, () => window.document.querySelectorAll(".pdf-item").length === PDFS.length);

  const javascriptUrl = PDFS.find((pdf) => pdf.id === "javascript").url;
  const breakoutUrl = PDFS.find((pdf) => pdf.id === "breakout").url;
  window.chrome.tabs.url = breakoutUrl;

  await window.handleSingleAnalysisFromUrl(javascriptUrl);
  await window.handleSingleAnalysis();
  await window.handleBatchAnalysis();
  if (typeof window.handleSingleAnalysisFromUrlFallback === "function") {
    await window.handleSingleAnalysisFromUrlFallback(javascriptUrl);
  }

  await window.renderPdfList();
  return { dom, storage };
}

function cellByKey(row, keys, key) {
  const index = keys.indexOf(key);
  assert(index >= 0, `column ${key} was not rendered`);
  return row.children[index];
}

function assertTextNotMarkup(cell, raw, label, problems) {
  if (cell.querySelector("img, form, iframe, object, embed, svg, script, a") != null) {
    problems.push(`${label} rendered stored analysis markup as HTML: ${cell.innerHTML}`);
  }
  if (!cell.textContent.includes(raw)) {
    problems.push(`${label} did not keep the analysis text visible (saw ${JSON.stringify(cell.textContent)})`);
  }
}

function assertDashboardSafe(document, label) {
  const problems = [];
  const keys = [...document.querySelectorAll("#tableHeaderRow th")].map((th) => th.dataset.columnKey);
  const rows = [...document.querySelectorAll("#formsTableBody tr")];
  if (rows.length !== PDFS.length) {
    problems.push(`${label} rendered ${rows.length} rows, expected ${PDFS.length}`);
  }

  const breakout = PAYLOADS.breakout;
  const breakoutRow = rows.find((row) => `${row.textContent} ${row.innerHTML}`.includes("beacon"));
  if (!breakoutRow) {
    problems.push(`${label} did not show the breakout analysis`);
  } else {
    for (const [column, field] of Object.entries(TEXT_COLUMNS)) {
      const raw = breakout[field];
      const needle = raw.includes("attacker.example")
        ? raw.match(/attacker\.example\/[a-z.-]+/)[0]
        : raw;
      assertTextNotMarkup(cellByKey(breakoutRow, keys, column), needle, `${label} ${column}`, problems);
    }
    const sourceCell = cellByKey(breakoutRow, keys, "source_url");
    if (sourceCell.querySelector("img") != null) {
      problems.push(`${label} source_url attribute breakout created an image`);
    }
    if (!sourceCell.textContent.includes("attacker.example/pixel.png")) {
      problems.push(`${label} source_url hid the stored URL text`);
    }
  }

  const httpsRow = rows.find((row) => `${row.textContent} ${row.innerHTML}`.includes(PAYLOADS.https.TITLE));
  if (!httpsRow) {
    problems.push(`${label} did not show the https analysis`);
  } else {
    const httpsCell = cellByKey(httpsRow, keys, "source_url");
    const httpsLinks = [...httpsCell.querySelectorAll("a")];
    if (httpsLinks.length !== 1) {
      problems.push(`${label} should keep one http(s) source link, found ${httpsLinks.length}`);
    } else if (httpsLinks[0].getAttribute("href") !== PAYLOADS.https.SOURCE) {
      problems.push(`${label} https href was ${httpsLinks[0].getAttribute("href")}`);
    }
    if (httpsCell.querySelector("img, form, script") != null) {
      problems.push(`${label} https cell contains extra markup`);
    }
  }

  const jsRow = rows.find((row) => `${row.textContent} ${row.innerHTML}`.includes(PAYLOADS.javascript.TITLE));
  if (!jsRow) {
    problems.push(`${label} did not show the javascript-url analysis`);
  } else {
    const jsCell = cellByKey(jsRow, keys, "source_url");
    if (jsCell.querySelector("a") != null) {
      problems.push(`${label} created a link for a javascript: source_url (${jsCell.querySelector("a").getAttribute("href")})`);
    }
    if (!jsCell.textContent.includes(PAYLOADS.javascript.SOURCE)) {
      problems.push(`${label} did not show the javascript source text`);
    }
  }
  if (document.querySelector("#formsTableBody img, #formsTableBody form, #formsTableBody script") != null) {
    problems.push(`${label} table still contains injected elements`);
  }
  assert(problems.length === 0, problems.join("\n"));
}

function assertPopupSafe(document) {
  const problems = [];
  const entity = [...document.querySelectorAll(".pdf-item-entity")]
    .find((node) => `${node.textContent} ${node.innerHTML}`.includes("entity.png"));
  if (!entity) problems.push("popup did not render the stored entity");
  else {
    if (entity.querySelector("img, form, a, script") != null) {
      problems.push(`popup rendered stored entity as HTML: ${entity.innerHTML}`);
    }
    if (!entity.textContent.includes("attacker.example/entity.png")) {
      problems.push("popup entity text was dropped");
    }
  }

  const tier = [...document.querySelectorAll(".pdf-item-status")]
    .find((node) => `${node.textContent} ${node.innerHTML}`.includes("attacker.example/tier.png"));
  if (!tier) problems.push("popup did not render the stored confidence tier");
  else if (tier.querySelector("img, form, script") != null) {
    problems.push(`popup rendered stored confidence_tier as HTML: ${tier.innerHTML}`);
  }
  assert(problems.length === 0, problems.join("\n"));
}

function assertReportSafe(html, label) {
  const dom = new JSDOM(html);
  const { document } = dom.window;
  assert(document.querySelector("img, form, script") == null,
    `${label} report rendered stored analysis as HTML`);
  const name = document.querySelector(".form-name");
  assert(name && name.textContent.includes("attacker.example/beacon.png"),
    `${label} report did not show the stored title as text`);
  const entityText = document.body.textContent;
  assert(entityText.includes("attacker.example/entity-name.png"),
    `${label} report did not show the stored entity name as text`);
}

async function renderReport(rootName, storedForms) {
  const dom = await loadPage(
    `${rootName}/popup/popup.html`,
    [`${rootName}/popup/reportGenerator.js`],
    memoryStorage({}),
    "chrome",
  );
  const forms = storedForms.map((form) => ({ ...form, pdf_url: form.source_url || "https://forms.example/application.pdf" }));
  const stats = dom.window.calculateStats(forms);
  return dom.window.generateReportHTML(forms, stats);
}

async function startServer() {
  const child = spawn("python3", [path.join(__dirname, "analysis_server.py")], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  child.stdout.on("data", (chunk) => { log += chunk; });
  child.stderr.on("data", (chunk) => { log += chunk; });
  const started = Date.now();
  while (Date.now() - started < 20000) {
    if (child.exitCode != null) {
      throw new Error(`analysis server exited ${child.exitCode}\n${log}`);
    }
    try {
      const response = await fetch(`${BASE}/api/analyze/health`);
      if (response.ok) return { child, log: () => log };
    } catch (error) {
      // server still binding
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  child.kill("SIGTERM");
  throw new Error(`analysis server did not start\n${log}`);
}

async function main() {
  const server = await startServer();
  const failures = [];
  try {
    const popupRuns = [];
    for (const rootName of POPUPS) {
      const run = await analyzeThroughPopup(rootName);
      popupRuns.push({ rootName, ...run });
      try {
        assertPopupSafe(run.dom.window.document);
        console.log(`PASS popup text rendering ${rootName}`);
      } catch (error) {
        failures.push(error);
        console.error(`FAIL popup text rendering ${rootName}: ${error.message}`);
      }
    }

    const stored = popupRuns[0].storage.snapshot();
    const storedForms = Object.entries(stored)
      .filter(([key, value]) => key.startsWith("analysis_") && value && !value.error)
      .map(([, value]) => value);
    assert(storedForms.length === PDFS.length, `expected ${PDFS.length} stored analyses, saw ${storedForms.length}`);
    assert(storedForms.some((form) => String(form.title || form.form_name || "").includes("beacon")),
      "upload path did not keep the PDF title text in extension storage");

    for (const dashboard of DASHBOARDS) {
      const storage = memoryStorage(stored);
      const namespace = dashboard.api;
      const dom = await loadPage(dashboard.html, dashboard.scripts, storage, namespace);
      await waitFor(dashboard.name, () => dom.window.document.querySelector("#formsTableBody tr"));
      try {
        assertDashboardSafe(dom.window.document, dashboard.name);
        console.log(`PASS dashboard text rendering ${dashboard.name}`);
      } catch (error) {
        failures.push(error);
        console.error(`FAIL dashboard text rendering ${dashboard.name}: ${error.message}`);
      }
    }

    for (const rootName of REPORTS) {
      try {
        const html = await renderReport(rootName, storedForms);
        assertReportSafe(html, rootName);
        console.log(`PASS report text rendering ${rootName}`);
      } catch (error) {
        failures.push(error);
        console.error(`FAIL report text rendering ${rootName}: ${error.message}`);
      }
    }
  } finally {
    server.child.kill("SIGTERM");
  }

  if (failures.length) {
    console.error(`\n${failures.length} assertion(s) failed`);
    process.exit(1);
  }
  console.log("\nall html-injection assertions passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
