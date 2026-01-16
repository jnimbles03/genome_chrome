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
    const manifest = chrome.runtime.getManifest();
    const currentVersion = manifest.version;

    // Check against backend
    const response = await fetch(`${API_BASE_URL}/version`);
    if (!response.ok) return;

    const data = await response.json();
    const latestVersion = data.version;

    if (isNewerVersion(latestVersion, currentVersion)) {
      console.log(`Update available: ${latestVersion} (Current: ${currentVersion})`);

      // Save update info
      await chrome.storage.local.set({
        updateAvailable: {
          version: latestVersion,
          message: data.message,
          url: data.download_url
        }
      });

      // Set global badge
      chrome.action.setBadgeText({ text: 'UPDT' });
      chrome.action.setBadgeBackgroundColor({ color: '#FF0000' });
    } else {
      // Clear any old update status if we are now up to date
      await chrome.storage.local.remove('updateAvailable');
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
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  try {
    if (message.type === 'PDF_DETECTED') {
      console.log('PDF detected:', message.url);
      // Could show a badge or notification here
      if (sender.tab?.id) {
        chrome.action.setBadgeText({ text: '✓', tabId: sender.tab.id });
        chrome.action.setBadgeBackgroundColor({ color: '#667eea', tabId: sender.tab.id });
      }
    } else if (message.type === 'PDF_LINK_CLICKED') {
      console.log('PDF link clicked:', message.url);
    }
  } catch (error) {
    console.error('Error handling message:', error);
  }
});

// Clear badge when tab is closed
chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.action.setBadgeText({ text: '', tabId });
});

// Update badge when switching tabs
chrome.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await chrome.tabs.get(activeInfo.tabId);
    const isPdf = tab.url && (
      tab.url.endsWith('.pdf') ||
      tab.url.includes('.pdf?') ||
      tab.url.includes('type=application/pdf')
    );

    if (isPdf) {
      chrome.action.setBadgeText({ text: '✓', tabId: tab.id });
      chrome.action.setBadgeBackgroundColor({ color: '#667eea', tabId: tab.id });
    } else {
      chrome.action.setBadgeText({ text: '', tabId: tab.id });
    }
  } catch (error) {
    console.error('Error updating badge:', error);
  }
});
