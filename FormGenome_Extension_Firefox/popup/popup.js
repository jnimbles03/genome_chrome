// Form Genome Chrome Extension - Popup Script with Batch Analysis

let currentPdfs = [];
let analysisCache = {};

// Scan for PDFs with retry mechanism
async function scanForPdfs(tabId, retries = 3, delay = 100) {
  for (let i = 0; i < retries; i++) {
    try {
      const response = await browser.tabs.sendMessage(tabId, { type: 'SCAN_FOR_PDFS' });

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
  const { apiPreference } = await browser.storage.local.get('apiPreference');
  const useLocal = apiPreference === 'local';

  // Override CONFIG based on preference
  if (useLocal) {
    CONFIG.API_URL = 'http://localhost:8080';
  }

  // Update current API display

  // Load analysis cache from storage
  const cacheData = await browser.storage.local.get(null);
  analysisCache = cacheData || {};

  // Get current tab
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });

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
    browser.tabs.create({ url: browser.runtime.getURL('dashboard/dashboard.html') });
  });
  document.getElementById('openTeamDashboard').addEventListener('click', () => {
    browser.tabs.create({ url: browser.runtime.getURL('dashboard/team-dashboard.html') });
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
    const { apiPreference } = await browser.storage.local.get('apiPreference');
    const isNowLocal = apiPreference !== 'local'; // Toggle

    if (isNowLocal) {
      CONFIG.API_URL = 'http://localhost:8080';
      await browser.storage.local.set({ apiPreference: 'local' });
      btnToggle.textContent = '🏠 Local';
      btnToggle.classList.add('local-mode');
      document.getElementById('localWarning').classList.remove('hidden');
    } else {
      CONFIG.API_URL = 'https://form-genome-nxqo233ggq-uc.a.run.app';
      await browser.storage.local.set({ apiPreference: 'cloud' });
      btnToggle.textContent = '☁️ Cloud';
      btnToggle.classList.remove('local-mode');
      document.getElementById('localWarning').classList.add('hidden');
    }

    // Optional: Visual confirmation or reload hint
    showSuccess(`Switched to ${isNowLocal ? 'Local' : 'Cloud'} API`);
  });

  // Check for update notification
  browser.storage.local.get('updateAvailable', (data) => {
    if (data.updateAvailable) {
      const { version, url } = data.updateAvailable;

