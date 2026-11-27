# app/api/analyze.py
# API endpoint for single file upload and analysis

from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
import os
import logging
import tempfile
import json
from typing import Dict, Any

# PDF processing
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

# LLM services
from app.services.title_llm import normalize_form_title, enforce_entity_consistency
from app.services.llm_router import chat_complete, LLMError
from app.services.remote_fetch import MAX_HTML_BYTES, open_stream, read_capped
from app.services.prompts import (
    SCOUT_SYSTEM_PROMPT, SCOUT_USER_PROMPT,
    ANALYST_SYSTEM_PROMPT, ANALYST_USER_PROMPT
)

logger = logging.getLogger(__name__)

# Create Blueprint
analyze_bp = Blueprint('analyze', __name__, url_prefix='/api/analyze')

# Allowed file extensions
ALLOWED_EXTENSIONS = {'pdf', 'txt', 'csv'}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# =============================================================================
# Helper Utilities
# =============================================================================

def analyze_url_logic(url: str):
    """Internal logic for analyzing a URL (follows redirects and peeks)"""
    import requests
    from io import BytesIO
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    import re

    # Robust headers to bypass 403 on enterprise sites
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }
    
    try:
        resp = open_stream(url, headers=headers, timeout=20)
        try:
            resp.raise_for_status()
            content_type = resp.headers.get('Content-Type', '').lower()
            if 'application/pdf' in content_type:
                body = read_capped(resp, MAX_FILE_SIZE)
            elif 'text/html' in content_type:
                body = read_capped(resp, MAX_HTML_BYTES, truncate=True)
        finally:
            resp.close()
        
        if 'application/pdf' in content_type:
            # It's a direct PDF
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                tmp.write(body)
                tmp_path = tmp.name
            try:
                results = analyze_pdf_file(tmp_path, url.split('/')[-1] or "downloaded.pdf", url=url)
                results['fileSize'] = len(body)
                return results
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        
        elif 'text/html' in content_type:
            # Deep Peek for PDF links
            soup = BeautifulSoup(body, 'html.parser')
            pdf_links = []
            
            # 1. Standard Anchor tags
            for a in soup.find_all('a', href=True):
                href = a['href']
                if href.lower().endswith('.pdf') or '.pdf?' in href.lower():
                    pdf_links.append(urljoin(url, href))
            
            # 2. Iframe sources (common for embedded viewers)
            for iframe in soup.find_all('iframe', src=True):
                src = iframe['src']
                if '.pdf' in src.lower():
                    pdf_links.append(urljoin(url, src))

            # 3. Regex for hidden URLs in scripts (e.g., PDF.js on Schwab)
            scripts = soup.find_all('script')
            for script in scripts:
                if not script.string: continue
                # Match things like "url": "https://... .pdf" or "source": "... .pdf"
                matches = re.findall(r'["\'](https?://[^"\']+\.pdf(?:\?[^"\']+)?)["\']', script.string, re.I)
                pdf_links.extend(matches)
            
            # Deduplicate
            pdf_links = list(dict.fromkeys(pdf_links))
            
            if not pdf_links:
                raise Exception("No PDF links found on landing page during Deep Peek")
            
            # Follow the first PDF link found
            target_pdf = pdf_links[0]
            logger.info(f"Deep Peek found PDF: {target_pdf}")
            resp2 = open_stream(target_pdf, headers=headers, timeout=20)
            try:
                resp2.raise_for_status()
                pdf_body = read_capped(resp2, MAX_FILE_SIZE)
            finally:
                resp2.close()
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                tmp.write(pdf_body)
                tmp_path = tmp.name
            try:
                results = analyze_pdf_file(tmp_path, target_pdf.split('/')[-1], url=target_pdf)
                results['fileSize'] = len(pdf_body)
                results['was_peaked'] = True
                results['original_url'] = url
                return results
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        else:
            raise Exception(f"Unsupported content type: {content_type}")
            
    except requests.exceptions.HTTPError as e:
        if '403' in str(e):
            logger.error(f"Deep Peek blocked by server (403 Forbidden): {url}")
            raise Exception(f"Access denied by server. This site may require browser-based JavaScript execution to reveal PDF sources. Try using the Chrome extension's 'Peek & Analyze' feature instead.")
        logger.error(f"Deep Peek HTTP error: {e}")
        raise e
    except Exception as e:
        logger.error(f"Deep Peek Analysis failed: {e}")
        raise e

def clean_text(text: str) -> str:
    """Clean extracted text to remove garbage and null bytes."""
    if not text: return ""
    
    # 1. Remove null bytes and non-printable characters
    import string
    text = "".join(filter(lambda x: x in string.printable or x in " \n\t", text))
    
    # 2. Repair common PDF encoding artifacts (garbage sequences like \000)
    import re
    # Match sequences of digits or characters separated by many zeros or weird patterns
    text = re.sub(r'000([a-zA-Z\d])', r'\1', text)
    
    # 3. Normalize whitespace
    text = " ".join(text.split())
    
    return text

# =============================================================================
# PDF Analysis
# =============================================================================

def analyze_pdf_file(file_path: str, filename: str, url: str = None) -> Dict[str, Any]:
    """
    Analyze a PDF file using Two-Stage LLM Workflow.
    
    Stage 1: Gemini Flash (Scout) - Quick assessment.
    Stage 2: Claude Opus (Deep) - Detailed field counting.
    """
    if not PdfReader:
        return {"error": "PDF library missing", "fileName": filename, "fileType": "PDF"}

    try:
        reader = PdfReader(file_path)
        num_pages = len(reader.pages)
        
        # Extract and clean text
        text_pages = []
        for i in range(min(5, num_pages)):
            try:
                t = reader.pages[i].extract_text()
                if t: text_pages.append(f"--- PAGE {i+1} ---\n{clean_text(t)}")
            except: pass
        
        full_text = "\n\n".join(text_pages)
        first_page = text_pages[0] if text_pages else ""

        # Metadata
        metadata = {}
        if reader.metadata:
            metadata = {k: v for k, v in reader.metadata.items() if k in ['/Title', '/Author']}

        # STAGE 1: GEMINI SCOUT
        logger.info(f"Stage 1 (Scout): Identifying complexity for {filename}")
        
        context_str = f"URL: {url}\nFilename: {filename}"
        
        scout_msg = [
            {"role": "system", "content": SCOUT_SYSTEM_PROMPT},
            {"role": "user", "content": SCOUT_USER_PROMPT.format(context=context_str, pages=num_pages, text=full_text[:10000])}
        ]
        
        s1_result = None
        try:
            resp1 = chat_complete(messages=scout_msg, max_tokens=600, temperature=0.0, timeout=15.0, force_tier2=False)
            import re
            c1 = re.sub(r'^```json\s*|```\s*$', '', resp1.strip(), flags=re.M|re.I)
            m1 = re.search(r'\{.*\}', c1, re.S)
            if m1: s1_result = json.loads(m1.group(0))
        except Exception as e:
            logger.warning(f"Stage 1 failed: {e}")

        # Decider: Should we verify with Deep Analysis (Stage 2)?
        # Scout is fast but shallow. If Scout sees ANY complexity, we must escalate.
        use_deep = True
        final_res = {}
        
        if s1_result:
            # Only trust Scout blindly if the form is trivial (e.g. simple memo)
            # Thresholds: Complexity < 20, No signatures, few fields
            score = s1_result.get('complexity_score', 0)
            sigs = s1_result.get('signature_required', False)
            fields = s1_result.get('total_field_count', 0)
            
            if score < 20 and not sigs and fields < 15:
                # truly simple
                logger.info(f"Stage 1 (Scout) sufficient. Score={score}, Sigs={sigs}, Fields={fields}")
                use_deep = False
                final_res = s1_result
            else:
                logger.info(f"Escalating to Stage 2 (Analyst). Scout Score={score}, Sigs={sigs}, Fields={fields}")
                # We do NOT use s1_result as final yet
        else:
            # If Scout failed to produce JSON, we assume it's tricky and try Stage 2
            logger.warning("Stage 1 failed to return valid JSON. Escalating to Stage 2.")
            use_deep = True

        # STAGE 2: CLAUDE DEEP
        if use_deep:
            logger.info("Stage 2 (Deep): Claude Analysis")
            deep_msg = [
                {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
                {"role": "user", "content": ANALYST_USER_PROMPT.format(context=context_str, text=full_text[:25000])}
            ]
            try:
                # Try Claude first (Tier 2)
                resp2 = chat_complete(messages=deep_msg, max_tokens=4000, temperature=0.0, timeout=60.0, force_tier2=True)
                import re
                c2 = re.sub(r'^```json\s*|```\s*$', '', resp2.strip(), flags=re.M|re.I)
                m2 = re.search(r'\{.*\}', c2, re.S)
                if m2:
                    final_res = json.loads(m2.group(0))
                    final_res['gemini_analyzed'] = False
                else: raise Exception("No JSON in Claude response")
            except Exception as e:
                logger.warning(f"Stage 2 (Claude) failed: {e}. Falling back to Gemini for Deep Analysis.")
                try:
                    # Fallback to Gemini (Tier 1) but with the same deep Analyst Prompt
                    # Increased tokens to prevent JSON truncation on large forms
                    resp2 = chat_complete(messages=deep_msg, max_tokens=8192, temperature=0.0, timeout=60.0, force_tier2=False)
                    import re
                    c2 = re.sub(r'^```json\s*|```\s*$', '', resp2.strip(), flags=re.M|re.I)
                    m2 = re.search(r'\{.*\}', c2, re.S)
                    if m2:
                        try:
                            # Try strict JSON first
                            final_res = json.loads(m2.group(0))
                            final_res['gemini_analyzed'] = True
                        except json.JSONDecodeError:
                            logger.warning(f"Malformed JSON from Gemini: {m2.group(0)[:200]}... Trying regex extraction.")
                            # Heuristic extraction
                            text = m2.group(0)
                            final_res = {}
                            
                            # Extract string fields
                            for key in ['title', 'entity', 'action_type', 'confidence_tier']:
                                m = re.search(f'"{key}"\s*:\s*"([^"]+)"', text)
                                if m: final_res[key] = m.group(1)
                            
                            # Extract int fields
                            for key in ['complexity_score', 'total_field_count', 'nigo_score']:
                                m = re.search(f'"{key}"\s*:\s*(\d+)', text)
                                if m: final_res[key] = int(m.group(1))

                            # Extract booleans
                            for key in ['signature_required', 'notarization_required']:
                                m = re.search(f'"{key}"\s*:\s*(true|false)', text, re.I)
                                if m: final_res[key] = m.group(1).lower() == 'true'
                                
                            final_res['gemini_analyzed'] = True
                            
                            if not final_res.get('title'): # If still empty, fail
                                raise Exception("Could not extract data from malformed JSON")
                    else:
                        logger.error(f"Gemini response (no JSON found): {resp2[:500]}")
                        raise Exception("No JSON in Gemini fallback")
                except Exception as e2:
                    logger.error(f"Stage 2 (Gemini Fallback) also failed: {e2}")
                    if s1_result: final_res = s1_result
                    else: final_res = {"title": normalize_form_title(filename), "entity": "Unknown", "error": "Deep analysis failed completely"}

        # Finalize title/entity
        title = final_res.get('title') or normalize_form_title(filename)
        entity = final_res.get('entity')
        
        # Enforce Entity Consistency (Domain Mapping)
        if url:
            consistent_entity = enforce_entity_consistency(entity, url)
            if consistent_entity:
                entity = consistent_entity
        
        if not entity or entity == "Unknown":
             entity = "Unknown"

        # Deterministic Scoring (Ironclad QC)
        from app.services.scoring import calculate_complexity_score
        # We pass the final_res data to the scoring engine
        # For now, we assume "Standard" weights unless modified by the user request later
        scoring_data = {
            "pages": num_pages,
            "total_field_count": final_res.get('total_field_count', 0),
            "signature_count": final_res.get('signature_count', 0),
            "notarization_required": final_res.get('notarization_required'),
            "payment_required": final_res.get('payment_required'),
            "attachments_required": final_res.get('attachments_required'),
            "identification_required": final_res.get('identification_required'),
            "conditional_logic": final_res.get('conditional_logic')
        }
        score_result = calculate_complexity_score(scoring_data)

        return {
            **final_res,
            "fileName": filename, "fileType": "PDF", "pages": num_pages,
            "fileName": filename, "fileType": "PDF", "pages": num_pages,
            "complexity": score_result["score"],
            "complexity_score": score_result["score"],  # FORCE overwrite LLM's raw prediction with our deterministic math
            "scoring_method": score_result["method"],
            "scoring_weights": score_result["weights"],
            "nigo_score": final_res.get('nigo_score', 0),
            "title": title, "entity": entity,
            "total_field_count": final_res.get('total_field_count'),
            "checkboxes": final_res.get('checkboxes'),
            "text_fields": final_res.get('text_fields'),
            "signature_analysis": {"signature_count": final_res.get('signature_count', 0)},
            "signature_required": final_res.get('signature_required', False) or (final_res.get('signature_count', 0) > 0),
            "action_type": final_res.get('action_type'),
            "notarization_required": final_res.get('notarization_required'),
            "attachments_required": final_res.get('attachments_required'),
            "attachment_count": final_res.get('attachment_count', 0),
            "payment_required": final_res.get('payment_required'),
            "payment_amount": final_res.get('payment_amount'),
            "identification_required": final_res.get('identification_required'),
            "conditional_logic": final_res.get('conditional_logic'),
            "third_party_involved": final_res.get('third_party_involved'),
            "witnesses_required": final_res.get('witnesses_required'),
            "deadlines_present": final_res.get('deadlines_present'),
            "form_purpose": final_res.get('form_purpose'),
            "industry_vertical": final_res.get('industry_vertical'),
            "industry_subvertical": final_res.get('industry_subvertical'),
            "estimated_signer_time": final_res.get('estimated_signer_time'),
            "estimated_signer_time": final_res.get('estimated_signer_time'),
            "estimated_processing_time": final_res.get('estimated_processing_time'),
            "gemini_analyzed": final_res.get('gemini_analyzed', True),
            "confidence_tier": final_res.get('confidence_tier', 'High'), # Ensure field exists for Popup UI
            "metadata": metadata,
            "preview": first_page[:500],
            "success": True
        }
    except Exception as e:
        logger.error(f"PDF Analysis Error: {e}")
        return {"error": str(e), "fileName": filename, "fileType": "PDF", "success": False}

# =============================================================================
# Text File Analysis
# =============================================================================

def analyze_txt_file(file_path: str, filename: str) -> Dict[str, Any]:
    """
    Analyze a text file.

    Args:
        file_path: Path to text file
        filename: Original filename

    Returns:
        Dictionary with analysis results
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        lines = content.split('\n')
        non_empty_lines = [line for line in lines if line.strip()]
        words = content.split()

        return {
            "fileName": filename,
            "fileType": "Text File",
            "lines": len(non_empty_lines),
            "words": len(words),
            "characters": len(content),
            "preview": content[:500],
            "success": True
        }

    except Exception as e:
        logger.error(f"Text analysis failed: {str(e)}")
        return {
            "error": str(e),
            "fileName": filename,
            "fileType": "Text File",
            "success": False
        }

# =============================================================================
# CSV File Analysis
# =============================================================================

def analyze_csv_file(file_path: str, filename: str) -> Dict[str, Any]:
    """
    Analyze a CSV file.

    Args:
        file_path: Path to CSV file
        filename: Original filename

    Returns:
        Dictionary with analysis results
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        lines = content.strip().split('\n')
        if len(lines) == 0:
            return {
                "error": "Empty CSV file",
                "fileName": filename,
                "fileType": "CSV File",
                "success": False
            }

        # Parse headers
        headers = [h.strip() for h in lines[0].split(',')]
        row_count = len(lines) - 1

        # Sample data
        sample_rows = lines[:6]  # Header + 5 data rows

        return {
            "fileName": filename,
            "fileType": "CSV File",
            "columns": len(headers),
            "rows": row_count,
            "headers": headers,
            "preview": '\n'.join(sample_rows),
            "success": True
        }

    except Exception as e:
        logger.error(f"CSV analysis failed: {str(e)}")
        return {
            "error": str(e),
            "fileName": filename,
            "fileType": "CSV File",
            "success": False
        }

# =============================================================================
# API Endpoints
# =============================================================================

@analyze_bp.route('/upload', methods=['POST'])
def upload_file():
    """
    Upload and analyze a file (PDF, TXT, or CSV).

    Request:
        - multipart/form-data with 'file' field

    Response:
    {
        "success": true,
        "results": {
            "fileName": "example.pdf",
            "fileType": "PDF",
            "pages": 5,
            "complexity": 45,
            "title": "Member Application Form",
            "entity": "Boeing Employees Credit Union",
            ...
        }
    }
    """
    try:
        # Check if URL is provided instead of file
        if request.is_json and 'url' in request.json:
            url = request.json['url']
            logger.info(f"Analyzing URL: {url}")
            try:
                results = analyze_url_logic(url)
                return jsonify({
                    "success": True,
                    "results": results
                })
            except Exception as e:
                return jsonify({
                    "success": False,
                    "error": str(e)
                }), 400

        if 'file' not in request.files:
            return jsonify({
                "success": False,
                "error": "No file provided"
            }), 400

        file = request.files['file']
        # Extract URL if provided in form data
        source_url = request.form.get('url')
        if source_url:
            logger.info(f"File upload received with Source URL: {source_url}")
        else:
            logger.warning("File upload received WITHOUT Source URL")

        # Check if file has a filename
        if file.filename == '':
            return jsonify({
                "success": False,
                "error": "No file selected"
            }), 400

        # Check if file type is allowed
        if not allowed_file(file.filename):
            return jsonify({
                "success": False,
                "error": "File type not allowed. Allowed types: PDF, TXT, CSV"
            }), 400

        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)

        if file_size > MAX_FILE_SIZE:
            return jsonify({
                "success": False,
                "error": f"File size exceeds {MAX_FILE_SIZE / (1024*1024):.0f}MB limit"
            }), 400

        # Save file to temporary location
        filename = secure_filename(file.filename)
        file_ext = filename.rsplit('.', 1)[1].lower()

        with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_ext}') as temp_file:
            temp_path = temp_file.name
            file.save(temp_path)

        try:
            # Analyze based on file type
            if file_ext == 'pdf':
                results = analyze_pdf_file(temp_path, filename, url=source_url)
            elif file_ext == 'txt':
                results = analyze_txt_file(temp_path, filename)
            elif file_ext == 'csv':
                results = analyze_csv_file(temp_path, filename)
            else:
                results = {
                    "error": "Unsupported file type",
                    "fileName": filename,
                    "success": False
                }

            # Add file size to results
            results['fileSize'] = file_size

            return jsonify({
                "success": results.get('success', True),
                "results": results
            })

        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file: {str(e)}")

    except Exception as e:
        logger.error(f"File upload failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@analyze_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "success": True,
        "status": "healthy",
        "supported_formats": list(ALLOWED_EXTENSIONS)
    })


# =============================================================================
# Blueprint Registration Helper
# =============================================================================

def register_analyze_api(app):
    """
    Register analyze API blueprint with Flask app.

    Usage:
        from flask import Flask
        from app.api.analyze import register_analyze_api

        app = Flask(__name__)
        register_analyze_api(app)
    """
    app.register_blueprint(analyze_bp)
    logger.info("Analyze API registered")
