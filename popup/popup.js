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
  document.getElementById('analyzeBatch').addEventListener('click', handleBatchAnalysis);
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
  currentPdfs = pdfs.filter(pdf => pdf && isHttpUrl(pdf.url));
  if (currentPdfs.length === 0) {
    showNoPdf();
    return;
  }
  document.getElementById('pdfList').classList.remove('hidden');
  hideAll(['singlePdf', 'noPdf', 'status', 'error', 'results']);

  renderPdfList();
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

    // Link text, URLs and cached analysis fields come from the scanned page or
    // the analysis API, so they are only ever assigned as text or attributes.
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `pdf_${index}`;
    checkbox.dataset.url = pdf.url;
    checkbox.dataset.action = pdf.action;
    checkbox.checked = !isAnalyzed;
    item.appendChild(checkbox);

    const info = document.createElement('div');
    info.className = 'pdf-item-info';

    const nameEl = document.createElement('div');
    nameEl.className = 'pdf-item-name';
    nameEl.title = pdf.text || '';

    const link = document.createElement('a');
    link.href = pdf.url;
    link.target = '_blank';
    link.className = 'form-link';
    link.title = 'Open form in new tab';
    link.textContent = pdf.text || '';
    nameEl.appendChild(link);

    // Determine badge text and style based on action
    if (pdf.language_count > 1) {
      nameEl.append(' ', createPdfBadge('lang-badge', `${pdf.language_count} language versions`, `+${pdf.language_count - 1} Langs`));
    }

    if (pdf.action === 'navigate') {
      nameEl.append(' ', createPdfBadge('lang-badge warning', 'URL is broken or blocked. Click the form link on the page first.', '⚠️ Click to Open'));
    } else if (pdf.action === 'peek') {
      nameEl.append(' ', createPdfBadge('lang-badge implicit', 'This is a landing page. Click to peek for PDF.', '🔍 Landing Page'));
    }
    info.appendChild(nameEl);

    if (cached && cached.entity) {
      const entityEl = document.createElement('div');
      entityEl.className = 'pdf-item-entity';
      entityEl.textContent = cached.entity;
      info.appendChild(entityEl);
    }

    const statusEl = document.createElement('div');
    statusEl.className = `pdf-item-status ${isAnalyzed ? 'analyzed' : ''} ${hasError ? 'error' : ''}`;
    statusEl.textContent = isAnalyzed ? (hasError ? 'Error' : `✓ Analyzed (${cached.confidence_tier || 'Ready'})`) : 'Not analyzed';
    info.appendChild(statusEl);
    item.appendChild(info);

    // Determine button based on action
    let actionButton = null;
    if (isAnalyzed && !hasError) {
      actionButton = createActionButton('btn-view-results', pdf.url, 'View');
    } else if (pdf.action === 'navigate') {
      actionButton = createActionButton('btn-view-results warning', pdf.url, 'Click Form Link', 'navigate');
      actionButton.disabled = true;
      actionButton.title = 'Click the form link on the page first';
    } else if (pdf.action === 'peek') {
      actionButton = createActionButton('btn-view-results peek', pdf.url, 'Peek & Analyze', 'peek');
    }
    if (actionButton) {
      item.appendChild(actionButton);
    }

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

function isHttpUrl(url) {
  if (typeof url !== 'string') return false;
  try {
    const { protocol } = new URL(url);
    return protocol === 'http:' || protocol === 'https:';
  } catch (e) {
    return false;
  }
}

function createPdfBadge(className, title, text) {
  const badge = document.createElement('span');
  badge.className = className;
  badge.title = title;
  badge.textContent = text;
  return badge;
}

function createActionButton(className, url, text, action) {
  const button = document.createElement('button');
  button.className = className;
  button.dataset.url = url;
  if (action) {
    button.dataset.action = action;
  }
  button.textContent = text;
  return button;
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
  showStatus('Peeking at landing page...');
  try {
    const result = await analyzePdf(url, true);
    const cacheKey = `analysis_${hashUrl(url)}`;
    result._cached_at = Date.now();
    analysisCache[cacheKey] = result;
    await chrome.storage.local.set({ [cacheKey]: result });

    displayResults(result);
  } catch (error) {
    showError(error.message || 'Failed to peek at landing page');
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

  for (const { pdf, action } of analyzablePdfs) {
    const url = pdf.url; // Use stable URL from state object, NOT dataset
    updateBatchStatus(completed, total, url);

    try {
      // Use action to determine analysis method
      const asUrl = action === 'peek';
      const result = await analyzePdf(url, asUrl);
      const cacheKey = `analysis_${hashUrl(url)}`;
      result._cached_at = Date.now();
      analysisCache[cacheKey] = result;
      await chrome.storage.local.set({ [cacheKey]: result });

      completed++;
      updateProgress((completed / total) * 100);
      renderPdfList();

    } catch (error) {
      console.error('Analysis failed:', error);
      const cacheKey = `analysis_${hashUrl(url)}`;
      analysisCache[cacheKey] = { error: error.message };

      completed++;
      updateProgress((completed / total) * 100);
      renderPdfList();
    }
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
