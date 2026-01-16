const fs = require('fs');
const path = require('path');

// Global state to track downloads
let lastDownload = {};
let blobMap = new Map(); // Map mock URL to content

// Setup mock environment (Manual Mocks)
global.window = {};
global.document = {
    body: {
        innerHTML: '',
        appendChild: () => { },
        removeChild: () => { }
    },
    createElement: (tag) => {
        if (tag === 'a') {
            return {
                href: '',
                download: '',
                click: function () {
                    // Intercept the click!
                    const content = blobMap.get(this.href);
                    lastDownload = {
                        filename: this.download,
                        content: content
                    };
                    console.log(`Download intercepted: ${this.download}`);
                },
                style: {},
                dataset: {} // Add dataset mock
            };
        }
        return { style: {}, innerHTML: '' };
    },
    getElementById: (id) => {
        if (id === 'badges') return { appendChild: () => { } };
        return null; // heuristic
    },
    querySelectorAll: () => [],
    querySelector: () => ({ innerHTML: '' })
};

global.Blob = class Blob {
    constructor(contentArray, options) {
        // Blob constructor takes an array of parts
        this.content = Array.isArray(contentArray) ? contentArray.join('') : contentArray;
        this.options = options;
    }
};

global.URL = {
    createObjectURL: (blob) => {
        const id = `blob:${Math.random()}`;
        blobMap.set(id, blob.content);
        return id;
    },
    revokeObjectURL: () => { }
};

global.alert = console.log;

// Mock chrome API for downloads
global.chrome = {
    downloads: {
        download: (options, callback) => {
            console.log(`Chrome Download Triggered: ${options.filename}`);
            // Retrieve content from blobMap using the blob URL mock
            const content = blobMap.get(options.url);
            lastDownload = {
                filename: options.filename,
                content: content
            };
            if (callback) callback(123); // Mock download ID
        }
    },
    runtime: {
        lastError: null
    }
};

// Load the report generator code
const reportGenPath = path.resolve('c:/code_projects/The Form Genome Project/extracted_chrome_share/popup/reportGenerator.js');
const reportGenCode = fs.readFileSync(reportGenPath, 'utf8');

// Evaluate the code in the global context
eval(reportGenCode);

// Mock Data
const mockPdfs = [
    { url: 'http://test.com/form1.pdf', text: 'Form 1', language_count: 1 },
    { url: 'http://test.com/form2.pdf', text: 'Form 2 (Spanish)', language_count: 2 }
];

const mockAnalysisCache = {
    'analysis_36401': { // hash for form1.pdf
        ok: true,
        entity_name: 'Test Entity',
        complexity_score: 50,
        signature_required: true,
        signature_analysis: { signature_count: 1 },
        confidence_tier: 'High'
    },
    'analysis_36402': { // hash for form2.pdf
        ok: true,
        entity_name: 'Test Entity',
        complexity_score: 80,
        notarization_required: true,
        payment_required: true,
        payment_amount: '100.00'
    }
};

// Mock `hashUrl` since it's in popup.js but needed here
global.hashUrl = (url) => {
    if (url.includes('form1')) return '36401';
    if (url.includes('form2')) return '36402';
    return '0';
};

// Test Runner
console.log('Starting QC Tests...');

// Test 1: Generate CSV
console.log('\nTest 1: CSV Generation');
lastDownload = {}; // Reset
try {
    generateCSVReport(mockAnalysisCache, mockPdfs);

    if (!lastDownload.content) throw new Error('No download triggered');
    if (!lastDownload.content.includes('Form Name,Entity Name')) throw new Error('Missing Headers');
    if (!lastDownload.content.includes('Form 1,Test Entity')) throw new Error('Missing Form 1 Data');
    if (!lastDownload.content.includes('Form 2 (Spanish),Test Entity')) throw new Error('Missing Form 2 Data');
    console.log('✅ CSV Generation Passed');
} catch (e) {
    console.error('❌ CSV Generation Failed:', e);
}

// Test 2: Generate HTML
console.log('\nTest 2: HTML Generation');
lastDownload = {}; // Reset
try {
    generateBatchReport(mockAnalysisCache, mockPdfs);

    if (!lastDownload.content) throw new Error('No download triggered');
    if (!lastDownload.content.includes('<!DOCTYPE html>')) throw new Error('Invalid HTML');
    if (!lastDownload.content.includes('Form Analysis Report — Test Entity')) throw new Error('Missing Title');
    if (!lastDownload.content.includes('Form 1')) throw new Error('Missing Form 1 Row');
    console.log('✅ HTML Generation Passed');
} catch (e) {
    console.error('❌ HTML Generation Failed:', e);
}

console.log('\nQC Complete.');
