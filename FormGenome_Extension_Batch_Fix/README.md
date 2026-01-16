# Form Genome Chrome Extension

Analyze PDF forms directly from your browser using Form Genome AI.

## Features

- 📄 **Two Modes**:
  - **Single PDF Mode**: Analyze a PDF you're viewing directly
  - **Batch Mode**: Detect all PDF links on a page and analyze them in batch
- ✅ **Select/Deselect PDFs**: Check/uncheck which forms to analyze (all selected by default)
- 🔄 **Smart Caching**: Already-analyzed PDFs are automatically unchecked and marked as "✓ Analyzed"
- 📊 **Progress Tracking**: See real-time progress bar during batch analysis
- 🔍 **Auto-Detection**: Finds PDF links even when they're added dynamically (infinite scroll, pagination)
- 🧬 **2-Stage LLM**: Uses Gemini + Anthropic for accurate analysis
- 💾 **Persistent Storage**: Results are saved across browser sessions
- 🎨 **Beautiful UI**: Purple gradient with smooth animations

## Installation

1. **Add Icons** (required):
   - Create or download 3 PNG icons: 16x16, 48x48, and 128x128 pixels
   - Save them in the `icons/` folder as `icon16.png`, `icon48.png`, and `icon128.png`
   - Suggested: Use a clipboard emoji 📋 or "FG" text on purple background (#667eea)

2. **Load Extension in Chrome**:
   - Open Chrome and go to `chrome://extensions/`
   - Enable "Developer mode" (toggle in top right)
   - Click "Load unpacked"
   - Select the `chrome-extension` folder
   - The extension icon should appear in your toolbar

3. **Test It**:
   - Navigate to any PDF file in your browser
   - Click the Form Genome extension icon
   - Click "Analyze This PDF"
   - View the analysis results!

## Configuration

Edit `config.js` to change the API URL:

```javascript
const CONFIG = {
  API_URL: 'https://form-genome-nxqo233ggq-uc.a.run.app',  // Production
  // API_URL: 'http://localhost:8080',  // Local development
  ANALYZE_ENDPOINT: '/api/analyze/upload'
};
```

## Files

- `manifest.json` - Extension configuration (Manifest V3)
- `config.js` - API URL configuration
- `popup/` - Extension popup UI
  - `popup.html` - Main interface
  - `popup.css` - Styling
  - `popup.js` - Analysis logic
- `content/content.js` - Detects PDFs on pages
- `background/background.js` - Service worker
- `icons/` - Extension icons (you need to add these)

## How It Works

### Single PDF Mode
1. Navigate to a PDF file in your browser
2. Extension badge shows a checkmark (✓) when PDF is detected
3. Click extension icon to open popup
4. Click "Analyze This PDF" button
5. Extension downloads the PDF and sends it to Form Genome API
6. Results are displayed and cached for future reference

### Batch Mode (Multiple PDFs on a Page)
1. Navigate to a page with PDF links (e.g., government forms directory)
2. Click extension icon - all PDF links are automatically detected
3. PDFs are listed with checkboxes (all checked by default)
4. Already-analyzed PDFs are automatically unchecked and show "✓ Analyzed"
5. Uncheck any PDFs you don't want to analyze
6. Click "Analyze Selected" to start batch analysis
7. Watch the progress bar as PDFs are analyzed one by one
8. Results are cached - revisit the page later and already-analyzed PDFs won't be re-analyzed

### Handling Pagination
- **Automatic Detection**: When you navigate to page 2, 3, etc., the extension automatically detects new PDF links
- **Smart Caching**: PDFs analyzed on page 1 remain cached when you visit page 2
- **Continuous Workflow**: Analyze page 1, navigate to page 2, analyze page 2 - the extension remembers everything
- **Deduplication**: If the same PDF appears on multiple pages, it's only analyzed once

## Permissions

- `activeTab` - Access current tab to detect PDFs
- `storage` - Cache analysis results
- Host permissions for Form Genome API (production and localhost)

## Troubleshooting

**Extension icon is gray/disabled:**
- Navigate to a PDF file first

**"Analysis failed" error:**
- Check that the Form Genome API is running
- Verify the API URL in `config.js`
- Check browser console for detailed error messages

**Icons not showing:**
- Make sure you added PNG files to the `icons/` folder
- Reload the extension after adding icons

## Development

To test with local Form Genome server:

1. Edit `config.js` and uncomment the localhost URL
2. Start local server: `python3 run_local.py`
3. Reload extension in Chrome
4. Test with a PDF

## Updates

After making changes:
1. Go to `chrome://extensions/`
2. Click the refresh icon on the Form Genome extension card
3. Reload any open PDF tabs
