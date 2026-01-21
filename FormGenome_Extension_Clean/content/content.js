// Form Genome Chrome Extension - Content Script
// Detects PDFs on the current page and finds all PDF links

// Check if current page is a PDF
const isPdfPage = () => {
  const url = window.location.href;
  return url.endsWith('.pdf') ||
    url.includes('.pdf?') ||
    url.includes('type=application/pdf') ||
    document.contentType === 'application/pdf';
};

// Cache for validation results to prevent network storms
const validationCache = new Map();

// Validate URL accessibility and type
async function validatePdfUrl(url) {
  if (validationCache.has(url)) {
    return validationCache.get(url);
  }

  const validationPromise = (async () => {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 3000);

      const response = await fetch(url, {
        method: 'HEAD',
        signal: controller.signal,
        mode: 'no-cors' // Avoid CORS issues for HEAD requests
      });

      clearTimeout(timeoutId);

      return {
        accessible: response.ok || response.type === 'opaque', // opaque = no-cors success
        contentType: response.headers.get('Content-Type'),
        status: response.status
      };
    } catch (error) {
      // Timeout or network error
      return { accessible: false, status: 0, error: error.message };
    }
  })();

  validationCache.set(url, validationPromise);
  return validationPromise;
}

// Find all PDF links on the page and group by language
async function findPdfLinks(rootElement = document) {
  const links = rootElement.querySelectorAll('a[href]');
  const pdfMap = new Map(); // Key: Base form name, Value: Object with details

  // Keywords that suggest a link is a form even if no .pdf extension
  const formKeywords = [
    '/form/', '/forms/', '/resource/', '/document/', 'form-', '-form',
    'application', 'authorization', 'instructions', 'permit',
    'request', 'guide', 'manual', 'checklist', 'agreement',
    'contract', 'report', 'survey', 'plan', 'packet'
  ];
  const excludeKeywords = ['.html', '.php', '.aspx', '.jsp', '#', 'javascript:'];

  links.forEach(link => {
    const href = link.href.toLowerCase();
    const text = link.textContent.trim();

    const isDirectPdf = href.endsWith('.pdf') || href.includes('.pdf?');
    const hasFormKeyword = formKeywords.some(kw => href.includes(kw) || text.toLowerCase().includes(kw));
    const isExcluded = excludeKeywords.some(kw => href.includes(kw)) && !isDirectPdf;

    if (href && (isDirectPdf || (hasFormKeyword && !isExcluded))) {
      // Extract filename or slug
      const urlParts = link.href.split('/');
      const rawFilename = urlParts[urlParts.length - 1].split('?')[0] || "Implicit Form";

      // Detect language
      // Patterns: _es, -es, _sp, -sp, _vie, -vie, (Spanish), (Español)
      const langRegex = /[-_](es|sp|vie|chi|rus|zho|kor|tag|hmn)[-_\.]|\((Spanish|Español|Vietnamese|Chinese|Russian|Korean|Tagalog)\)/i;
      let isNonEnglish = langRegex.test(rawFilename) || langRegex.test(text);

      // --- NEW: Proximity Language Detection (for Schwab-style siblings) ---
      // Check the next few siblings for language indicators
      if (!isNonEnglish) {
        let sibling = link.nextSibling;
        let searchDepth = 3;
        while (sibling && searchDepth > 0) {
          const sibText = sibling.textContent || "";
          if (langRegex.test(sibText)) {
            // We found a language sibling, this suggests the current link is part of a group
            // We don't mark THIS link as non-English, but we note it for grouping later
          }
          sibling = sibling.nextSibling;
          searchDepth--;
        }
      }

      // Determine base name (remove language suffix for grouping)
      let baseName = rawFilename.replace(langRegex, '').replace(/[-_]\.pdf$/i, '.pdf');

      // Generate a grouping key
      const groupKey = baseName.replace(/\.pdf$/i, '').toLowerCase().trim() || text.toLowerCase().trim();

      if (!pdfMap.has(groupKey)) {
        pdfMap.set(groupKey, {
          url: link.href,
          filename: rawFilename,
          text: text || rawFilename,
          language_count: 1,
          is_english: !isNonEnglish,
          is_implicit: !isDirectPdf,
          variations: [rawFilename],
          action: 'unknown' // Will be determined by validation
        });
      } else {
        const entry = pdfMap.get(groupKey);
        entry.language_count += 1;
        if (!entry.variations.includes(rawFilename)) {
          entry.variations.push(rawFilename);
        }

        // If the existing entry was non-English and this one IS English, swap them
        if (!entry.is_english && !isNonEnglish) {
          entry.url = link.href;
          entry.filename = rawFilename;
          entry.text = text || rawFilename;
          entry.is_english = true;
          entry.is_implicit = !isDirectPdf;
        }
      }
    }
  });

  // Validate URLs for implicit forms
  const pdfs = Array.from(pdfMap.values());
  const validationPromises = pdfs.map(async (pdf) => {
    if (pdf.is_implicit) {
      const validation = await validatePdfUrl(pdf.url);

      if (!validation.accessible) {
        // URL is broken/truncated (404) or blocked (403)
        pdf.action = 'navigate'; // User must click link first
        pdf.badge_type = 'warning';
      } else if (validation.contentType && validation.contentType.includes('pdf')) {
        // Direct PDF that's accessible
        pdf.action = 'analyze';
        pdf.is_implicit = false; // Actually a direct PDF
        pdf.badge_type = 'success';
      } else {
        // Landing page with valid URL
        pdf.action = 'peek';
        pdf.badge_type = 'info';
      }
    } else {
      // Direct PDF link
      pdf.action = 'analyze';
      pdf.badge_type = 'success';
    }
    return pdf;
  });

  return await Promise.all(validationPromises);
}

function findPaginationLinks() {
  const urls = new Set();
  const links = document.querySelectorAll('a[href]');

  links.forEach(link => {
    const text = link.textContent.trim().toLowerCase();
    const rel = (link.getAttribute('rel') || '').toLowerCase();

    // Check standard pagination patterns
    if (rel === 'next' || rel === 'prev') {
      urls.add(link.href);
    } else if (text === 'next' || text === 'previous' || text === 'next page' || text === 'previous page' || text === '>' || text === '<') {
      urls.add(link.href);
    } else if (text.match(/^\d+$/) && link.href.includes('page')) {
      // Numbered pagination (e.g., "2", "3")
      // Check if it's close to current (heuristic)
      urls.add(link.href);
    }
  });

  // Limit to reasonable neighbors to prevent crawling the whole site
  // Ideally we want just the immediately adjacent pages
  return Array.from(urls).slice(0, 3);
}


// Find directory-like links that likely contain forms
function findDirectoryLinks() {
  const urls = new Set();
  const links = document.querySelectorAll('a[href]');

  // Keywords that suggest a link is a container for forms
  const directoryKeywords = [
    'forms', 'applications', 'documents', 'resources', 'packets',
    'permits', 'contracts', 'agreements', 'manuals', 'guidelines'
  ];

  // Specific directory exclusions
  const excludeKeywords = [
    'login', 'signin', 'register', 'contact', 'about', 'privacy',
    'terms', 'policy', 'javascript:', 'mailto:', 'tel:', '#'
  ];

  links.forEach(link => {
    const href = link.href.toLowerCase();
    const text = link.textContent.trim().toLowerCase();

    // Must not be a PDF itself (those are handled by findPdfLinks)
    if (href.includes('.pdf')) return;

    const isMatch = directoryKeywords.some(kw => href.includes(kw) || text.includes(kw));
    const isExcluded = excludeKeywords.some(kw => href.includes(kw));

    if (isMatch && !isExcluded) {
      urls.add(link.href);
    }
  });

  // Limit to 5 most likely directories to prevent crawling entire site
  return Array.from(urls).slice(0, 5);
}

// Perform Deep Scan (Fetch standard neighbors + Directory links)
async function performDeepScan() {
  const paginationUrls = findPaginationLinks();
  const directoryUrls = findDirectoryLinks();

  // Combine unique URLs
  const targetUrls = new Set([...paginationUrls, ...directoryUrls]);
  let allPdfs = [];

  console.log('[Deep Scan] Targets:', targetUrls);

  for (const url of targetUrls) {
    try {
      // Request background to fetch
      const response = await new Promise(resolve => {
        chrome.runtime.sendMessage({ type: 'FETCH_PAGE', url: url }, resolve);
      });

      if (response && response.success) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(response.html, 'text/html');

        // Set base URI so relative links resolve correctly
        const base = doc.createElement('base');
        base.href = url;
        doc.head.appendChild(base);

        // Scan this document
        const pdfs = await findPdfLinks(doc);
        const isDirectory = directoryUrls.includes(url);

        // Tag them
        pdfs.forEach(p => {
          p.source_page = isDirectory ? "Smart Scan" : "Neighbor";
          p.badge_type = "deep-scan"; // Use distinct badge
          // Add parent URL for context
          p.parent_url = url;
        });

        allPdfs = [...allPdfs, ...pdfs];
      }
    } catch (e) {
      console.error('Deep scan error for', url, e);
    }
  }

  return allPdfs;
}

// Helper function to safely send messages (handles extension reload)
function safeSendMessage(message) {
  try {
    chrome.runtime.sendMessage(message, (response) => {
      // Check for errors (like extension context invalidated)
      if (chrome.runtime.lastError) {
        // Extension was reloaded - silently ignore
        console.debug('Extension reloaded, ignoring message:', chrome.runtime.lastError.message);
      }
    });
  } catch (error) {
    // Extension context invalidated - silently ignore
    console.debug('Extension context invalidated:', error.message);
  }
}

// Wait for DOM to be fully ready before initial scan
async function performInitialScan() {
  if (isPdfPage()) {
    safeSendMessage({
      type: 'PDF_DETECTED',
      url: window.location.href,
      isPdfPage: true
    });
  } else {
    // Send list of PDF links found on page
    const pdfLinks = await findPdfLinks();
    if (pdfLinks.length > 0) {
      safeSendMessage({
        type: 'PDF_LINKS_FOUND',
        pdfs: pdfLinks,
        pageUrl: window.location.href
      });
    }
  }
}

// Perform initial scan when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', performInitialScan);
} else {
  // DOM already loaded
  performInitialScan();
}

// Listen for messages from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'SCAN_FOR_PDFS') {
    if (isPdfPage()) {
      sendResponse({
        isPdfPage: true,
        url: window.location.href
      });
    } else {
      // Handle async findPdfLinks
      findPdfLinks().then(pdfLinks => {
        sendResponse({
          isPdfPage: false,
          pdfs: pdfLinks,
          pageUrl: window.location.href
        });
      });
      return true; // Keep channel open for async response
    }
  } else if (request.type === 'START_DEEP_SCAN') {
    performDeepScan().then(deepPdfs => {
      sendResponse({
        success: true,
        pdfs: deepPdfs
      });
    });
    return true;
  }
});

// Watch for dynamically added PDF links (for infinite scroll, pagination, etc.)
let lastPdfCount = 0;

const observer = new MutationObserver(() => {
  // Debounce the observer to prevent network storms during rapid loading
  clearTimeout(window.scanDebounceTimer);
  window.scanDebounceTimer = setTimeout(async () => {
    const currentPdfs = await findPdfLinks();
    if (currentPdfs.length !== lastPdfCount) {
      lastPdfCount = currentPdfs.length;
      safeSendMessage({
        type: 'PDF_LINKS_UPDATED',
        pdfs: currentPdfs,
        pageUrl: window.location.href
      });
    }
  }, 500); // Wait 500ms after last mutation
});

// Start observing for DOM changes
observer.observe(document.body, {
  childList: true,
  subtree: true
});
