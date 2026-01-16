# Drag and Drop UI Fix - Documentation

## Issue
The drag and drop UI existed but wasn't connected to a backend API to actually analyze files. It was returning mock data.

## Solution
Created a new `/api/analyze/upload` endpoint that accepts file uploads and analyzes them using LLM-based extraction for PDFs and basic analysis for TXT/CSV files.

## Changes Made

### 1. New Backend API Endpoint
**File**: `app/api/analyze.py` (NEW)

Features:
- Accepts file uploads via multipart/form-data
- Supports PDF, TXT, and CSV files
- Maximum file size: 50MB
- PDF analysis includes:
  - Page count
  - Complexity scoring (0-100)
  - LLM-based title and entity extraction
  - Text preview
  - Metadata extraction
- TXT analysis includes:
  - Line count
  - Word count
  - Character count
  - Content preview
- CSV analysis includes:
  - Column count
  - Row count
  - Header extraction
  - Data preview

### 2. Updated Main Application
**File**: `main.py`

Changes:
- Imported `register_analyze_api`
- Registered analyze API blueprint
- Updated endpoints list in index route
- Added `/ui` route to serve the UI HTML file

### 3. Updated API Module Exports
**File**: `app/api/__init__.py`

Changes:
- Added `analyze_bp` and `register_analyze_api` exports

### 4. Fixed UI JavaScript
**File**: `ui/index.html`

Changes:
- Updated `analyzePDF`, `analyzeTXT`, and `analyzeCSV` functions to call backend API
- Added `analyzeFile` function that uploads file to `/api/analyze/upload`
- Added `API_BASE_URL` configuration (auto-detects localhost vs production)
- Improved `displayResults` function to handle backend response format
- Added proper error handling for API failures
- Added HTML escaping for preview text
- Added metadata display for PDF files

## How to Test

### 1. Start the Flask Server

```bash
cd "/Users/patrickmeyer/My Drive (patrick.meyer@docusign.com)/The Form Genome Project/form-genome-structured-cloudrun"

# Make sure environment variables are set
# GEMINI_API_KEY and ANTHROPIC_API_KEY in .env file

# Start the server
python3 main.py
```

Server will start on `http://localhost:8080`

### 2. Open the UI

Open your browser and navigate to:
```
http://localhost:8080/ui
```

### 3. Test Drag and Drop

**Test with PDF:**
1. Drag a PDF form into the drop zone
2. Click "Analyze Form"
3. Should see:
   - File name
   - File type: PDF
   - File size
   - Page count
   - Complexity score (0-100)
   - Extracted title (cleaned and normalized)
   - Extracted entity name (full legal name)
   - Text preview from first page
   - PDF metadata (if available)

**Test with TXT:**
1. Drag a .txt file into the drop zone
2. Click "Analyze Form"
3. Should see:
   - File name
   - File type: Text File
   - Line count
   - Word count
   - Character count
   - Content preview

**Test with CSV:**
1. Drag a .csv file into the drop zone
2. Click "Analyze Form"
3. Should see:
   - File name
   - File type: CSV File
   - Column count
   - Row count
   - Header names
   - Data preview (first 5 rows)

### 4. Test API Directly

**Upload a PDF:**
```bash
curl -X POST http://localhost:8080/api/analyze/upload \
  -F "file=@/path/to/your/form.pdf"
```

**Check health:**
```bash
curl http://localhost:8080/api/analyze/health
```

## API Endpoints

### Upload and Analyze File
```
POST /api/analyze/upload
Content-Type: multipart/form-data

Request:
- file: PDF, TXT, or CSV file (max 50MB)

Response:
{
  "success": true,
  "results": {
    "fileName": "example.pdf",
    "fileType": "PDF",
    "fileSize": 1234567,
    "pages": 5,
    "complexity": 45,
    "title": "Member Application Form",
    "entity": "Boeing Employees Credit Union",
    "preview": "First 500 characters of text...",
    "metadata": {
      "title": "...",
      "author": "...",
      "subject": "...",
      "creator": "..."
    }
  }
}
```

### Health Check
```
GET /api/analyze/health

Response:
{
  "success": true,
  "status": "healthy",
  "supported_formats": ["pdf", "txt", "csv"]
}
```

## Error Handling

The UI now properly handles errors:

1. **No file selected**: Shows error message
2. **Invalid file type**: Shows "File type not allowed" error
3. **File too large**: Shows "File size exceeds 50MB limit" error
4. **Backend API down**: Shows clear error message with API error details
5. **Analysis failure**: Shows specific error from backend

## Architecture

```
┌─────────────────┐
│   Browser UI    │
│  (index.html)   │
└────────┬────────┘
         │ POST multipart/form-data
         │ /api/analyze/upload
         ▼
┌─────────────────┐
│   Flask API     │  ← app/api/analyze.py
│  (Port 8080)    │
└────────┬────────┘
         │
         ├──→ PDF Processing (pypdf)
         │     ├─ Extract text
         │     ├─ Count pages
         │     └─ Extract metadata
         │
         ├──→ LLM Analysis (title_llm.py)
         │     ├─ Tier 1: Gemini Flash
         │     └─ Tier 2: Claude Opus (fallback)
         │
         └──→ TXT/CSV Analysis
               └─ Basic counting and parsing
```

## Files Created/Modified

### Created:
1. `app/api/analyze.py` (379 lines) - New file upload and analysis API
2. `DRAG_DROP_FIX.md` (this file) - Documentation

### Modified:
1. `main.py` - Added analyze API registration and UI route
2. `app/api/__init__.py` - Added analyze API exports
3. `ui/index.html` - Fixed analysis functions to call backend API

## Troubleshooting

### "PDF processing library not available"
Install pypdf:
```bash
pip install pypdf
```

### "Connection refused" when analyzing files
Make sure Flask server is running on port 8080:
```bash
python3 main.py
```

### LLM extraction fails
Check that API keys are set in `.env`:
```bash
GEMINI_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
```

### Files not uploading
Check browser console for CORS errors. CORS is enabled for `/api/*` in main.py.

## Performance

- **PDF Analysis**: ~2-5 seconds (depends on LLM response time)
- **TXT Analysis**: <1 second (local processing)
- **CSV Analysis**: <1 second (local processing)

For PDF files, the LLM call adds latency but provides intelligent title and entity extraction.

## Security

- File size limited to 50MB
- Only PDF, TXT, CSV extensions allowed
- Files are saved to temporary directory and deleted after analysis
- Filenames are sanitized using `secure_filename`
- HTML output is escaped to prevent XSS

## Next Steps

Potential improvements:
1. Add progress indicator for LLM analysis
2. Add batch upload (multiple files at once)
3. Add download results as JSON
4. Add history of analyzed files
5. Add preview thumbnails for PDFs
6. Add support for more file types (DOCX, XLSX, etc.)

---

**Status**: ✅ Drag and drop functionality is now fully working and connected to backend API.
