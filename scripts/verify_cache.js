const fs = require('fs');
const path = require('path');

// Setup mock environment
global.window = {};
global.document = {
    getElementById: (id) => {
        if (id === 'pdfItems') return { innerHTML: '', appendChild: () => { } };
        if (id === 'selectedCount' || id === 'totalCount') return { textContent: '' };
        if (id === 'error') return { classList: { remove: () => { }, add: () => { } }, style: {} };
        if (id === 'errorText') return { style: {}, textContent: '' };
        return {
            classList: { remove: () => { }, add: () => { } },
            addEventListener: () => { }
        };
    },
    querySelectorAll: () => [],
    createElement: () => ({ innerHTML: '', querySelector: () => ({ addEventListener: () => { } }) }),
    addEventListener: () => { }
};

// Mock Chrome Storage
let storage = { 'analysis_123': { some: 'data' } };
global.chrome = {
    storage: {
        local: {
            get: async () => storage,
            set: async (data) => { Object.assign(storage, data); },
            clear: async () => { storage = {}; console.log('Storage Cleared'); }
        }
    },
    tabs: { query: async () => [{ url: 'http://test.com' }] },
    runtime: { getURL: () => '' }
};

// Mock CONFIG
global.CONFIG = { API_URL: '' };

// Mock global variables expected by popup.js
global.currentPdfs = [];
global.analysisCache = { 'analysis_123': { some: 'data' } }; // Initial state

// Mock confirm
global.confirm = () => true;

// Load the popup code (we only need the handleClearCache function really)
// Since popup.js has a lot of DOM interaction, we'll just extract or eval it.
// Eval might fail if it runs immediate setup code.
// Let's just mock the function logic itself to verify *logic* correctness if I were to copy it, 
// OR I can try to eval popup.js but suppress the `document.addEventListener` call at the end?
// popup.js runs `document.addEventListener('DOMContentLoaded', ...)` immediately.
// My mock document.addEventListener will catch that.

const popupPath = path.resolve('c:/code_projects/The Form Genome Project/extracted_chrome_share/popup/popup.js');
let popupCode = fs.readFileSync(popupPath, 'utf8');

// Strip local declarations so it uses our globals
popupCode = popupCode.replace('let currentPdfs = [];', '// let currentPdfs = [];');
popupCode = popupCode.replace('let analysisCache = {};', '// let analysisCache = {};');

try {
    eval(popupCode);
} catch (e) {
    // Ignore errors from immediate execution finding missing DOM elements
    // We just want the functions defined.
    // console.log('Eval warning (expected):', e.message);
}

// Test Runner
console.log('Starting Cache QC Tests...');

async function runTest() {
    console.log('\nTest 1: Clear Cache');
    console.log('Initial Storage:', Object.keys(storage).length + ' items');

    // Check initial state
    if (Object.keys(storage).length === 0) throw new Error('Setup failed: Storage empty');

    // Run clear cache
    // handleClearCache is async
    await handleClearCache();

    // Verify
    if (Object.keys(storage).length !== 0) throw new Error('Storage NOT cleared');
    if (Object.keys(analysisCache).length !== 0) throw new Error('analysisCache global variable NOT cleared');

    console.log('✅ Cache Clearing Passed');
}

runTest().catch(e => console.error('❌ Cache Clearing Failed:', e));
