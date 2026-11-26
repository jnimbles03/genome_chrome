// Form Genome Chrome Extension - Popup Script with Batch Analysis

let currentPdfs = [];
let analysisCache = {};

// Scan for PDFs with retry mechanism
async function scanForPdfs(tabId, retries = 3, delay = 100) {
  for (let i = 0; i < retries; i++) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: 'SCAN_FOR_PDFS' });

      if (response && response.isPdfPage) {
        // Single PDF mode
        showSinglePdfMode(response.url);
        return;
      } else if (response && response.pdfs && response.pdfs.length > 0) {
        // Multiple PDFs found on page
        showPdfListMode(response.pdfs);
        return;
      } else {
        showNoPdf();
        return;
      }
    } catch (error) {
      // Content script not ready, wait and retry
      if (i < retries - 1) {
        await new Promise(resolve => setTimeout(resolve, delay));
        delay *= 2; // Exponential backoff
      } else {
        // Final attempt failed - content script may not be injected
        showNoPdf();
      }
    }
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  // Load API preference from storage
  const { apiPreference } = await chrome.storage.local.get('apiPreference');
  const useLocal = apiPreference === 'local';

  // Override CONFIG based on preference
  if (useLocal) {
    CONFIG.API_URL = 'http://localhost:8080';
  }

  // Update current API display

  // Load analysis cache from storage
  const cacheData = await chrome.storage.local.get(null);
  analysisCache = cacheData || {};

  // Get current tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  // Request PDF scan from content script with retry mechanism
  await scanForPdfs(tab.id);

  // Set up event listeners
  document.getElementById('selectAll').addEventListener('change', handleSelectAll);
  document.getElementById('selectAll').addEventListener('change', handleSelectAll);
  document.getElementById('analyzeBatch').addEventListener('click', handleBatchAnalysis);
  document.getElementById('deepScan').addEventListener('click', handleDeepScan);
  document.getElementById('analyzeSingle').addEventListener('click', handleSingleAnalysis);
  document.getElementById('clearCache').addEventListener('click', handleClearCache);
  document.getElementById('generateReport').addEventListener('click', () => {
    generateBatchReport(analysisCache, currentPdfs);
  });
  document.getElementById('generateCSV').addEventListener('click', () => {
    generateCSVReport(analysisCache, currentPdfs);
  });

  // Test Download Button Logic
  const testDLBtn = document.getElementById('testDownload');
  if (testDLBtn) {
    testDLBtn.addEventListener('click', () => {
      console.log('[FORM GENOME] Test download clicked');
      // Verify download via anchor tag
      if (typeof downloadViaAnchor === 'function') {
        downloadViaAnchor('Hello World - Test Download', 'text/plain', 'test_download.txt');
      } else {
        alert('Report Generator not loaded yet!');
      }
    });
  }
  document.getElementById('openDashboard').addEventListener('click', () => {
    chrome.tabs.create({ url: chrome.runtime.getURL('dashboard/dashboard.html') });
  });
  document.getElementById('openTeamDashboard').addEventListener('click', () => {
    chrome.tabs.create({ url: chrome.runtime.getURL('dashboard/team-dashboard.html') });
  });

  // Settings panel event listeners
  // Ensure correct initial state
  const btnToggle = document.getElementById('toggleApi');
  if (useLocal) {
    btnToggle.textContent = '🏠 Local';
    btnToggle.classList.add('local-mode');
    document.getElementById('localWarning').classList.remove('hidden');
  } else {
    btnToggle.textContent = '☁️ Cloud';
    btnToggle.classList.remove('local-mode');
    document.getElementById('localWarning').classList.add('hidden');
  }

  // Toggle API preference on click
  btnToggle.addEventListener('click', async () => {
    const { apiPreference } = await chrome.storage.local.get('apiPreference');
    const isNowLocal = apiPreference !== 'local'; // Toggle

    if (isNowLocal) {
      CONFIG.API_URL = 'http://localhost:8080';
      await chrome.storage.local.set({ apiPreference: 'local' });
      btnToggle.textContent = '🏠 Local';
      btnToggle.classList.add('local-mode');
      document.getElementById('localWarning').classList.remove('hidden');
    } else {
      CONFIG.API_URL = 'https://form-genome-nxqo233ggq-uc.a.run.app';
      await chrome.storage.local.set({ apiPreference: 'cloud' });
      btnToggle.textContent = '☁️ Cloud';
      btnToggle.classList.remove('local-mode');
      document.getElementById('localWarning').classList.add('hidden');
    }

    // Optional: Visual confirmation or reload hint
    showSuccess(`Switched to ${isNowLocal ? 'Local' : 'Cloud'} API`);
  });

  // Check for update notification
  chrome.storage.local.get('updateAvailable', (data) => {
    if (data.updateAvailable) {
      const { version, url } = data.updateAvailable;
      const notifyEl = document.getElementById('updateNotification');
      const versionEl = document.getElementById('newVersion');
      const linkEl = document.getElementById('updateLink');

      versionEl.textContent = version;
      linkEl.href = url;
      notifyEl.classList.remove('hidden');
    }
  });
});

function showNoPdf() {
  document.getElementById('noPdf').classList.remove('hidden');
  hideAll(['pdfList', 'singlePdf', 'status', 'error', 'results']);
}

function showSinglePdfMode(url) {
  document.getElementById('singlePdf').classList.remove('hidden');
  document.getElementById('currentPdfTitle').textContent = extractFilename(url);
  hideAll(['pdfList', 'noPdf', 'status', 'error']);

  // Check if we have cached analysis
  const cacheKey = `analysis_${hashUrl(url)}`;
  if (analysisCache[cacheKey]) {
    displayResults(analysisCache[cacheKey]);
  }
}

function showPdfListMode(pdfs) {
  currentPdfs = pdfs;
  document.getElementById('pdfList').classList.remove('hidden');
  hideAll(['singlePdf', 'noPdf', 'status', 'error', 'results']);

  renderPdfList();
}

function createListElement(tag, className, text, title) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  if (title !== undefined) element.title = title;
  return element;
}

function toSafeHttpUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null;
  } catch (e) {
    return null;
  }
}

function renderPdfList() {
  const container = document.getElementById('pdfItems');
  container.innerHTML = '';

  currentPdfs.forEach((pdf, index) => {
    const cacheKey = `analysis_${hashUrl(pdf.url)}`;
    const cached = analysisCache[cacheKey];

    // Check if cache is expired (older than 7 days)
    const cacheAge = cached?._cached_at ? Date.now() - cached._cached_at : Infinity;
    const cacheExpired = cacheAge > (7 * 24 * 60 * 60 * 1000); // 7 days in ms

    // SIMPLIFIED LOGIC: If it's in cache and not expired, it's analyzed.
    // Removed strict 'confidence_tier' check to prevent UI sync issues.
    const isAnalyzed = !!cached && !cacheExpired;
    const hasError = cached?.error;

    // Debugging mismatch
    if (cached && !isAnalyzed) {
      console.warn('[FORM GENOME] Cache ignored:', { url: pdf.url, cacheKey, cacheAge, cacheExpired });
    }

    const item = document.createElement('div');
    item.className = 'pdf-item';

    // Page-supplied values (pdf.text, pdf.url, cached.*) must only reach the DOM as text.
    const badges = [];
    if (pdf.language_count > 1) {
      badges.push(createListElement('span', 'lang-badge', `+${pdf.language_count - 1} Langs`, `${pdf.language_count} language versions`));
    }

    if (pdf.action === 'navigate') {
      badges.push(createListElement('span', 'lang-badge warning', '⚠️ Click to Open', 'URL is broken or blocked. Click the form link on the page first.'));
    } else if (pdf.action === 'peek') {
      badges.push(createListElement('span', 'lang-badge implicit', '🔍 Landing Page', 'This is a landing page. Click to peek for PDF.'));
    }

    if (pdf.badge_type === 'deep-scan') {
      badges.push(createListElement('span', 'lang-badge deep-scan', 'Neighbor Page', 'Found on neighboring page'));
    }

    // Determine button based on action
    let actionButton = null;
    if (isAnalyzed && !hasError) {
      actionButton = createListElement('button', 'btn-view-results', 'View');
    } else if (pdf.action === 'navigate') {
      actionButton = createListElement('button', 'btn-view-results warning', 'Click Form Link', 'Click the form link on the page first');
      actionButton.dataset.action = 'navigate';
      actionButton.disabled = true;
    } else if (pdf.action === 'peek') {
      actionButton = createListElement('button', 'btn-view-results peek', 'Peek & Analyze');
      actionButton.dataset.action = 'peek';
    }
    if (actionButton) actionButton.dataset.url = pdf.url;

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `pdf_${index}`;
    checkbox.dataset.url = pdf.url;
    checkbox.dataset.action = pdf.action;
    checkbox.checked = !isAnalyzed;

    const info = createListElement('div', 'pdf-item-info');

    const nameDiv = createListElement('div', 'pdf-item-name', undefined, pdf.text);
    const link = createListElement('a', 'form-link', pdf.text, 'Open form in new tab');
    const safeUrl = toSafeHttpUrl(pdf.url);
    if (safeUrl) link.href = safeUrl;
    link.target = '_blank';
    nameDiv.append(link, ' ', ...badges);
    info.appendChild(nameDiv);

    if (cached && cached.entity) {
      info.appendChild(createListElement('div', 'pdf-item-entity', cached.entity));
    }

    const status = createListElement(
      'div',
      'pdf-item-status',
      isAnalyzed ? (hasError ? 'Error' : `✓ Analyzed (${cached.confidence_tier || 'Ready'})`) : 'Not analyzed'
    );
    if (isAnalyzed) status.classList.add('analyzed');
    if (hasError) status.classList.add('error');
    info.appendChild(status);

    item.append(checkbox, info);
    if (actionButton) item.appendChild(actionButton);

    item.querySelector('input[type="checkbox"]').addEventListener('change', updateSelectAllState);

    // Add click handler for "View" or "Peek" button
    const viewBtn = item.querySelector('.btn-view-results');
    if (viewBtn) {
      viewBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const action = viewBtn.dataset.action;
        if (action === 'peek') {
          handleSingleAnalysisFromUrl(pdf.url);
        } else {
          const cacheKey = `analysis_${hashUrl(pdf.url)}`;
          displayResults(analysisCache[cacheKey]);
        }
      });
    }

    container.appendChild(item);
  });

  updateSelectAllState();
  updateSelectedCount();
}

function updateSelectAllState() {
  const checkboxes = document.querySelectorAll('.pdf-item input[type="checkbox"]');
  const selectAllCheckbox = document.getElementById('selectAll');
  const checkedCount = Array.from(checkboxes).filter(cb => cb.checked).length;

  selectAllCheckbox.checked = checkedCount === checkboxes.length;
  selectAllCheckbox.indeterminate = checkedCount > 0 && checkedCount < checkboxes.length;

  updateSelectedCount();
}

function updateSelectedCount() {
  const checkboxes = document.querySelectorAll('.pdf-item input[type="checkbox"]');
  const checkedCount = Array.from(checkboxes).filter(cb => cb.checked).length;
  document.getElementById('selectedCount').textContent = checkedCount;
  document.getElementById('totalCount').textContent = checkboxes.length;
}

function handleSelectAll(event) {
  const checkboxes = document.querySelectorAll('.pdf-item input[type="checkbox"]');
  checkboxes.forEach(cb => {
    cb.checked = event.target.checked;
  });
  updateSelectedCount();
}

async function handleSingleAnalysisFromUrl(url) {
  showStatus('Peeking at landing page locally...');
  try {
    // 1. Fetch page via background (Client-side fetch to bypass 403)
    const response = await chrome.runtime.sendMessage({ type: 'FETCH_PAGE', url: url });

    if (!response || !response.success) {
      console.warn('Client-side fetch failed, falling back to Server:', response?.error);
      return await handleSingleAnalysisFromUrlFallback(url);
    }

    // 2. Parse HTML
    const parser = new DOMParser();
    const doc = parser.parseFromString(response.html, 'text/html');

    // 3. Find PDF Link
    let pdfUrl = null;

    // Strategy A: Direct links
    const links = Array.from(doc.querySelectorAll('a[href]'));
    for (const link of links) {
      // Resolve relative URLs
      try {
        const absoluteUrl = new URL(link.getAttribute('href'), url).href;
        if (absoluteUrl.toLowerCase().endsWith('.pdf') || absoluteUrl.toLowerCase().includes('.pdf?')) {
          pdfUrl = absoluteUrl;
          break;
        }
      } catch (e) { }
    }

    // Strategy B: Iframes
    if (!pdfUrl) {
      const iframes = Array.from(doc.querySelectorAll('iframe[src]'));
      for (const iframe of iframes) {
        try {
          const absoluteUrl = new URL(iframe.getAttribute('src'), url).href;
          if (absoluteUrl.toLowerCase().endsWith('.pdf')) {
            pdfUrl = absoluteUrl;
            break;
          }
        } catch (e) { }
      }
    }

    // Strategy C: Regex (Hidden JS links)
    if (!pdfUrl) {
      const regex = /["'](https?:\/\/[^"']+\.pdf(?:\?[^"']+)?)["']/i;
      const match = response.html.match(regex);
      if (match) pdfUrl = match[1];
    }

    if (!pdfUrl) {
      throw new Error('No hidden PDF link found on this page.');
    }

    showStatus(`Found PDF: ${extractFilename(pdfUrl)}. Downloading & Analyzing...`);

    // 4. Analyze found PDF (Client-side download + Upload)
    const result = await analyzePdf(pdfUrl, false);

    const cacheKey = `analysis_${hashUrl(url)}`;
    result._cached_at = Date.now();
    result.was_peaked = true;
    result.original_url = url;

    analysisCache[cacheKey] = result;
    await chrome.storage.local.set({ [cacheKey]: result });

    displayResults(result);

  } catch (error) {
    console.error('Peek failed:', error);
    showError(error.message || 'Failed to peek at landing page');
  }
}

async function handleSingleAnalysisFromUrlFallback(url) {
  console.log('Using Server-side Peek fallback');
  try {
    const result = await analyzePdf(url, true);
    const cacheKey = `analysis_${hashUrl(url)}`;
    result._cached_at = Date.now();
    analysisCache[cacheKey] = result;
    await chrome.storage.local.set({ [cacheKey]: result });
    displayResults(result);
  } catch (error) {
    showError(error.message);
  }
}

async function handleBatchAnalysis() {
  const checkboxes = document.querySelectorAll('.pdf-item input[type="checkbox"]:checked');
  const selectedPdfs = Array.from(checkboxes).map(cb => {
    const index = cb.id.split('_')[1];
    return {
      url: cb.dataset.url,
      action: cb.dataset.action,
      pdf: currentPdfs[index]
    };
  });

  if (selectedPdfs.length === 0) {
    showError('Please select at least one PDF to analyze');
    return;
  }

  // Filter out forms that require navigation
  const navigateForms = selectedPdfs.filter(p => p.action === 'navigate');
  if (navigateForms.length > 0) {
    showError(`${navigateForms.length} form(s) require you to click the link on the page first. Skipping those.`);
  }

  const analyzablePdfs = selectedPdfs.filter(p => p.action !== 'navigate');
  if (analyzablePdfs.length === 0) {
    showError('No analyzable forms selected. Please click the form links on the page first.');
    return;
  }

  // Show batch status
  document.getElementById('batchStatus').classList.remove('hidden');
  document.getElementById('analyzeBatch').disabled = true;

  let completed = 0;
  const total = analyzablePdfs.length;

  // Parallel Processing Configuration
  const BATCH_SIZE = 3;

  async function processItem(item) {
    const { pdf, action } = item;
    const url = pdf.url;

    try {
      // Use action to determine analysis method
      // For batch processing, we use standard analysis to ensure speed.
      // Complex peek logic is reserved for single-item analysis to avoid timeouts.

      const asUrl = action === 'peek';
      const result = await analyzePdf(url, asUrl);
      const cacheKey = `analysis_${hashUrl(url)}`;
      result._cached_at = Date.now();
      analysisCache[cacheKey] = result;
      await chrome.storage.local.set({ [cacheKey]: result });

    } catch (error) {
      console.error('Analysis failed:', error);
      const cacheKey = `analysis_${hashUrl(url)}`;
      analysisCache[cacheKey] = { error: error.message };
    } finally {
      completed++;
      updateBatchStatus(completed, total, url);
      updateProgress((completed / total) * 100);
      renderPdfList(); // Update UI incrementally
    }
  }

  // Process in chunks
  for (let i = 0; i < analyzablePdfs.length; i += BATCH_SIZE) {
    const chunk = analyzablePdfs.slice(i, i + BATCH_SIZE);
    await Promise.all(chunk.map(item => processItem(item)));
  }

  // Complete
  document.getElementById('batchStatus').classList.add('hidden');
  document.getElementById('analyzeBatch').disabled = false;
  updateSelectAllState();
  updateSelectedCount();

  showSuccess(`Analyzed ${completed} items successfully!`);
}

async function analyzePdf(pdfUrl, asUrl = false) {
  console.log('[FORM GENOME] Starting analysis for:', pdfUrl, asUrl ? '(as URL)' : '(as File)');

  const apiUrl = `${CONFIG.API_URL}${CONFIG.ANALYZE_ENDPOINT}`;

  if (asUrl) {
    const response = await fetch(apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: pdfUrl })
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.error || `Analysis failed: ${response.statusText}`);
    }
    const data = await response.json();
    return data.results || data;
  }

  // Download PDF
  const response = await fetch(pdfUrl);
  if (!response.ok) {
    throw new Error(`Failed to download PDF: ${response.statusText}`);
  }

  const blob = await response.blob();
  const formData = new FormData();
  formData.append('file', blob, extractFilename(pdfUrl));
  formData.append('url', pdfUrl); // Pass URL for domain consistency checks

  const analysisResponse = await fetch(apiUrl, {
    method: 'POST',
    body: formData
  });

  if (!analysisResponse.ok) {
    throw new Error(`Analysis failed: ${analysisResponse.statusText}`);
  }

  const data = await analysisResponse.json();
  return data.results || data;
}

async function handleSingleAnalysis() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  showStatus('Analyzing PDF...');

  try {
    const result = await analyzePdf(tab.url);
    const cacheKey = `analysis_${hashUrl(tab.url)}`;
    result._cached_at = Date.now();
    analysisCache[cacheKey] = result;
    await chrome.storage.local.set({ [cacheKey]: result });

    displayResults(result);
  } catch (error) {
    showError(error.message || 'Failed to analyze PDF');
  }
}

function updateBatchStatus(completed, total, currentUrl) {
  const filename = extractFilename(currentUrl);
  document.getElementById('batchStatusText').textContent =
    `Analyzing ${completed + 1} of ${total}: ${filename}`;
}

function updateProgress(percent) {
  document.getElementById('progressFill').style.width = `${percent}%`;
}

function showStatus(message) {
  document.getElementById('status').classList.remove('hidden');
  document.getElementById('statusText').textContent = message;
  hideAll(['error', 'results', 'pdfList', 'singlePdf', 'noPdf']);
}

function showError(message) {
  document.getElementById('error').classList.remove('hidden');
  document.getElementById('errorText').textContent = message;
  document.getElementById('status').classList.add('hidden');

  setTimeout(() => {
    document.getElementById('error').classList.add('hidden');
  }, 5000);
}

function showSuccess(message) {
  // Reuse error element for success messages
  const errorEl = document.getElementById('error');
  errorEl.classList.remove('hidden');
  errorEl.style.background = '#e8f5e9';
  errorEl.style.borderColor = '#a5d6a7';
  document.getElementById('errorText').style.color = '#2e7d32';
  document.getElementById('errorText').textContent = message;

  setTimeout(() => {
    errorEl.classList.add('hidden');
    errorEl.style.background = '';
    errorEl.style.borderColor = '';
    document.getElementById('errorText').style.color = '';
  }, 3000);
}

function displayResults(data) {
  // Debug: Log the actual data received
  console.log('[FORM GENOME] Displaying results:', {
    signature_required: data.signature_required,
    signature_count: data.signature_analysis?.signature_count,
    entity_name: data.entity_name,
    entity: data.entity,
    action_type: data.action_type
  });

  document.getElementById('results').classList.remove('hidden');
  document.getElementById('status').classList.add('hidden');

  // Auto-scroll to results
  setTimeout(() => {
    document.getElementById('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 100);

  // Title - use pretty_title or form_name, not the filename
  document.getElementById('formTitle').textContent = data.pretty_title || data.form_name || data.title || 'Untitled Form';

  // Stats
  document.getElementById('fieldCount').textContent = data.total_field_count || data.field_count || 0;
  document.getElementById('pages').textContent = data.pages || 0;
  document.getElementById('complexity').textContent = data.complexity_score || 0;

  // Details - use entity_name which has the full name
  const entity = data.entity_name || data.entity || 'Unknown';
  document.getElementById('entity').textContent = entity;

  // Show simple action type description
  const actionDesc = data.signature_required ? 'Signature Required' : 'Information Only';
  document.getElementById('actionType').textContent = actionDesc;

  // Show signature requirement clearly
  const sigCount = data.signature_analysis?.signature_count || 0;
  const sigText = data.signature_required
    ? `Yes (${sigCount} signature${sigCount !== 1 ? 's' : ''})`
    : 'No';
  document.getElementById('signatures').textContent = sigText;

  document.getElementById('confidence').textContent =
    `${data.confidence_tier || 'unknown'}`;

  // Badges
  const badgesContainer = document.getElementById('badges');
  badgesContainer.innerHTML = '';

  if (data.gemini_analyzed) {
    addBadge('✓ Gemini Analyzed', 'success');
  }

  if (data.signature_required) {
    addBadge('Signature Required', 'warning');
  }

  if (data.notarization_required) {
    addBadge('Notarization Required', 'warning');
  }

  if (data.attachments_required) {
    addBadge('Attachments Required', 'warning');
  }

  if (data.identification_required) {
    addBadge('ID Required', 'warning');
  }
}

function addBadge(text, type = 'default') {
  const badge = document.createElement('span');
  badge.className = `badge ${type}`;
  badge.textContent = text;
  document.getElementById('badges').appendChild(badge);
}

function hideAll(ids) {
  ids.forEach(id => {
    document.getElementById(id).classList.add('hidden');
  });
}

function extractFilename(url) {
  const urlParts = url.split('/');
  return urlParts[urlParts.length - 1].split('?')[0] || 'document.pdf';
}

function hashUrl(url) {
  // Normalize URL to handle encoding discrepancies (e.g. %20 vs space)
  const normalized = decodeURIComponent(url).trim();
  let hash = 0;
  for (let i = 0; i < normalized.length; i++) {
    const char = normalized.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash;
  }
  return Math.abs(hash).toString(36);
}

async function handleClearCache() {
  if (!confirm('Clear all cached analysis results? You will need to re-analyze PDFs to see their results again.')) {
    return;
  }

  // Clear all storage
  await chrome.storage.local.clear();
  analysisCache = {};

  console.log('[FORM GENOME] Cache cleared! analysisCache is now empty:', analysisCache);

  // Refresh the list
  renderPdfList();

  showSuccess('Cache cleared! Select PDFs to re-analyze them.');
}

// Make clearCache available in console for debugging
window.forceClearCache = async function () {
  await chrome.storage.local.clear();
  analysisCache = {};
  console.log('[FORM GENOME] Force cleared! Reload the popup to see changes.');
};

async function handleDeepScan() {
  const deepScanBtn = document.getElementById('deepScan');
  deepScanBtn.disabled = true;
  deepScanBtn.textContent = 'Scanning Neighbors...';

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  try {
    const response = await chrome.tabs.sendMessage(tab.id, { type: 'START_DEEP_SCAN' });

    if (response && response.success && response.pdfs) {
      const newPdfs = response.pdfs;

      // Merge with existing
      const existingUrls = new Set(currentPdfs.map(p => p.url));
      let addedCount = 0;

      newPdfs.forEach(pdf => {
        if (!existingUrls.has(pdf.url)) {
          currentPdfs.push(pdf);
          addedCount++;
        }
      });

      if (addedCount > 0) {
        showSuccess(`Deep Scan found ${addedCount} new forms!`);
        renderPdfList();
      } else {
        showSuccess('Deep Scan complete. No new unique forms found.');
      }

    } else {
      showError('Deep Scan failed or found no pagination links.');
    }
  } catch (error) {
    console.error('Deep scan error:', error);
    showError('Deep Scan error: ' + error.message);
  } finally {
    deepScanBtn.disabled = false;
    deepScanBtn.textContent = 'Deep Scan';
  }
}
