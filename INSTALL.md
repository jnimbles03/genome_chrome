# Installation Instructions

## Form Genome PDF Analyzer Chrome Extension

This guide provides clear and concise steps to install and use the Form Genome PDF Analyzer Chrome extension.

---

## Prerequisites

Before installing the extension, ensure you have:

- **Google Chrome** (version 88 or later) or any Chromium-based browser (Edge, Brave, etc.)
- An internet connection for cloud-based PDF analysis

---

## Installation Steps

### Option 1: Install from Chrome Web Store (Recommended)

*Note: This option will be available once the extension is published to the Chrome Web Store.*

1. Visit the Chrome Web Store page for Form Genome PDF Analyzer
2. Click the **"Add to Chrome"** button
3. Confirm the installation by clicking **"Add extension"** in the popup dialog
4. The extension icon will appear in your browser toolbar

### Option 2: Install from Source (Developer Mode)

If you want to install the extension from source code or for development purposes:

1. **Download the Extension**
   - Clone the repository or download the ZIP file
   - If downloaded as ZIP, extract it to a folder on your computer

2. **Open Chrome Extensions Page**
   - Open Google Chrome
   - Navigate to `chrome://extensions/`
   - Or click the menu (⋮) → **More tools** → **Extensions**

3. **Enable Developer Mode**
   - Toggle the **"Developer mode"** switch in the top-right corner

4. **Load the Extension**
   - Click the **"Load unpacked"** button
   - Navigate to the folder containing the extension files
   - Select the folder and click **"Select Folder"** (or "Open" on some systems)

5. **Verify Installation**
   - The Form Genome extension should now appear in your extensions list
   - You should see the extension icon (📋) in your browser toolbar
   - If the icon is not visible, click the extensions icon (puzzle piece) and pin Form Genome

---

## Configuration

### API Endpoint Selection

The extension supports two modes:

- **Cloud Mode (Default)**: Uses the cloud-hosted Form Genome API
- **Local Mode**: Uses a local development server (for developers)

To switch between modes:
1. Click the extension icon in your toolbar
2. Click the **"☁️ Cloud"** button in the header to toggle between Cloud and Local modes
3. When using Local mode, ensure your local server is running on port 8080

---

## How to Use

### Analyzing PDFs on a Webpage

1. **Navigate to a webpage** that contains PDF links
2. **Click the Form Genome extension icon** in your browser toolbar
3. The extension will automatically **detect all PDF links** on the page
4. **Select the PDFs** you want to analyze (or use "Select All")
5. Click the **"Analyze Selected"** button
6. Wait for the analysis to complete
7. **View the results** for each PDF, including:
   - Field count
   - Page count
   - Complexity level
   - Entity type
   - Action type
   - Required signatures
   - Confidence score

### Analyzing a Single PDF

1. **Open a PDF file** directly in Chrome (or click a PDF link)
2. **Click the Form Genome extension icon**
3. Click the **"Analyze This PDF"** button
4. View the analysis results

### Managing Results

- **Clear Cache**: Click the "🗑️ Clear" button to remove cached results
- **Generate Report**: Click the "📊 Report" button to create a summary report
- **Export CSV**: Click the "📄 CSV" button to download results as a CSV file
- **My Dashboard**: Click "📊 My Dashboard" to view your analysis history
- **Team Dashboard**: Click "👥 Team" to access team-wide analytics

---

## Permissions

The extension requires the following permissions:

- **activeTab**: To detect and analyze PDFs on the current page
- **storage**: To cache analysis results and save preferences
- **downloads**: To export reports and CSV files
- **Host permissions**: To communicate with the Form Genome API servers

---

## Troubleshooting

### Extension Icon Not Visible

- Click the extensions icon (puzzle piece) in your toolbar
- Find "Form Genome PDF Analyzer" and click the pin icon

### No PDFs Detected

- Ensure you're on a page that contains PDF links
- Try refreshing the page
- Some PDFs embedded in iframes may not be detected

### Analysis Fails

- Check your internet connection
- If using Local mode, ensure your local server is running on port 8080
- Try switching between Cloud and Local modes
- Clear the cache and try again

### Local Server Warning

If you see "⚠️ Ensure Local Server (port 8080) is Running":
- This appears when in Local mode
- Either start your local server on port 8080, or
- Switch back to Cloud mode by clicking the toggle button

---

## Uninstalling

To remove the extension:

1. Navigate to `chrome://extensions/`
2. Find "Form Genome PDF Analyzer"
3. Click **"Remove"**
4. Confirm the removal

---

## Support

For issues, questions, or feature requests, please visit the project repository or contact the development team.

---

## Version Information

- **Current Version**: 1.0.0
- **Manifest Version**: 3
- **Minimum Chrome Version**: 88+
