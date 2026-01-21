// Form Genome Chrome Extension - Background Service Worker

// Import config first to define API_BASE_URL
importScripts('../config.js');

// Import sync service
importScripts('../services/sync.js');

// Initialize sync on extension startup
initializeSync();
checkUpdate();

// Check for updates
async function checkUpdate() {
  try {
    const manifest = browser.runtime.getManifest();
    const currentVersion = manifest.version;

    // Check against backend
    const response = await fetch(`${API_BASE_URL}/version`);
    if (!response.ok) return;

    const data = await response.json();
    const latestVersion = data.version;

    if (isNewerVersion(latestVersion, currentVersion)) {
      console.log(`Update available: ${latestVersion} (Current: ${currentVersion})`);

      // Save update info
      await browser.storage.local.set({
        updateAvailable: {
          version: latestVersion,
          message: data.message,
          url: data.download_url
        }
      });

      // Set global badge
      browser.action.setBadgeText({ text: 'UPDT' });
      browser.action.setBadgeBackgroundColor({ color: '#FF0000' });
    } else {
      // Clear any old update status if we are now up to date
      await browser.storage.local.remove('updateAvailable');
    }
  } catch (error) {
    console.error('Update check failed:', error);
  }
}

function isNewerVersion(latest, current) {
  const l = latest.split('.').map(Number);
  const c = current.split('.').map(Number);
  for (let i = 0; i < 3; i++) {
    if (l[i] > c[i]) return true;
    if (l[i] < c[i]) return false;
  }
  return false;
}

// Listen for messages from content scripts
browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
  try {
    if (message.type === 'PDF_DETECTED') {
      console.log('PDF detected:', message.url);
      // Could show a badge or notification here
      if (sender.tab?.id) {
        browser.action.setBadgeText({ text: '✓', tabId: sender.tab.id });
        browser.action.setBadgeBackgroundColor({ color: '#667eea', tabId: sender.tab.id });
      }
    } else if (message.type === 'PDF_LINK_CLICKED') {
      console.log('PDF link clicked:', message.url);
    } else if (message.type === 'FETCH_PAGE') {
      // Fetch external page content (bypassing CORS for content script)
      fetch(message.url)
        .then(response => response.text())
        .then(text => {
          sendResponse({ success: true, html: text });
        })
        .catch(error => {
          console.error('Fetch failed:', error);
          sendResponse({ success: false, error: error.message });
        });
      return true; // Keep channel open for async response
    }
  } catch (error) {
    console.error('Error handling message:', error);
  }
});

// Clear badge when tab is closed
browser.tabs.onRemoved.addListener((tabId) => {
  browser.action.setBadgeText({ text: '', tabId });
});

// Update badge when switching tabs
browser.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await browser.tabs.get(activeInfo.tabId);
    const isPdf = tab.url && (
      tab.url.endsWith('.pdf') ||
      tab.url.includes('.pdf?') ||
      tab.url.includes('type=application/pdf')
    );

    if (isPdf) {
      browser.action.setBadgeText({ text: '✓', tabId: tab.id });
      browser.action.setBadgeBackgroundColor({ color: '#667eea', tabId: tab.id });
    } else {
      browser.action.setBadgeText({ text: '', tabId: tab.id });
    }
  } catch (error) {
    console.error('Error updating badge:', error);
  }
});

