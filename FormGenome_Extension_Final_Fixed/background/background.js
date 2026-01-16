// Form Genome Chrome Extension - Background Service Worker

// Import config first to define API_BASE_URL
importScripts('../config.js');

// Import sync service
importScripts('../services/sync.js');

// Initialize sync on extension startup
initializeSync();

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
