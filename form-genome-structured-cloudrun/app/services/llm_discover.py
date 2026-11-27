# app/services/llm_discover.py
# LLM-based PDF discovery with two-tier routing
from __future__ import annotations

import os
import re
import json
import logging
import urllib.parse as up
from typing import List, Dict, Any, Tuple

from app.services.llm_router import chat_complete, LLMError
from app.services.remote_fetch import MAX_HTML_BYTES, get_capped

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Robust JSON parsing
# -----------------------------------------------------------------------------
def parse_json_robust(text: str) -> dict:
    """
    Robustly parse JSON from LLM response, handling common issues.

    Common LLM JSON issues:
    - Unterminated strings (missing closing quote)
    - Trailing commas
    - Unescaped quotes in URLs
    - Missing closing braces

    Returns empty dict with empty lists if parsing fails completely.
    """
    if not text:
        return {"pdf_urls": [], "paths": [], "expected_total": 0}

    # Strip markdown code fences
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', text, flags=re.MULTILINE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned, flags=re.MULTILINE)

    # Extract JSON object
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if not m:
        logger.warning("No JSON object found in LLM response")
        return {"pdf_urls": [], "paths": [], "expected_total": 0}

    json_str = m.group(0)

    # Try parsing as-is first
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed: {str(e)[:200]}")

        # Attempt 1: Fix unterminated strings by adding closing quote before newline/brace
        try:
            fixed = re.sub(r'("https?://[^"]*?)(\n|\})', r'\1"\2', json_str)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # Attempt 2: Remove trailing commas
        try:
            fixed = re.sub(r',(\s*[}\]])', r'\1', json_str)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # Attempt 3: Extract just the pdf_urls array
        try:
            pdf_match = re.search(r'"pdf_urls"\s*:\s*\[([\s\S]*?)\]', json_str)
            if pdf_match:
                # Extract URLs from the array
                urls_text = pdf_match.group(1)
                urls = re.findall(r'"(https?://[^"]+\.pdf[^"]*)"', urls_text)
                logger.info(f"Fallback: Extracted {len(urls)} URLs from malformed JSON")
                return {"pdf_urls": urls, "paths": [], "expected_total": len(urls)}
        except Exception:
            pass

        # Final fallback: return empty
        logger.error("All JSON parsing attempts failed, returning empty result")
        return {"pdf_urls": [], "paths": [], "expected_total": 0}

# -----------------------------------------------------------------------------
# HTTP helpers
# -----------------------------------------------------------------------------
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "close",
}

PDF_RE = re.compile(r"https?://[^\s\"'<>]+\.pdf(?:[?#][^\s\"'<>\]]*)?", re.I)

def _norm(u: str) -> str:
    if not u:
        return u
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    p = up.urlsplit(u)
    path = up.quote(up.unquote(p.path), safe="/:@-._~!$&'()*+,;=")
    return up.urlunsplit((p.scheme or "https", p.netloc, path, p.query, p.fragment))

def fetch(url: str, timeout: float = 10.0) -> Tuple[int, str, Dict[str, str]]:
    """Fetch a single URL (HTML)."""
    r = get_capped(url, MAX_HTML_BYTES, truncate=True, headers=BROWSER_HEADERS, timeout=timeout)
    return r.status_code, (r.text or ""), {k.lower(): v for k, v in r.headers.items()}

def try_sitemap(seed: str, timeout: float = 8.0) -> List[str]:
    """Return a (possibly empty) list of absolute URLs found in sitemap files."""
    out: List[str] = []
    try:
        p = up.urlsplit(seed)
        host = f"{p.scheme}://{p.netloc}"
        for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
            s = host + path
            try:
                code, text, _ = fetch(s, timeout=timeout)
                if code // 100 == 2 and ("</urlset>" in text or "</sitemapindex>" in text):
                    out.extend(re.findall(r"https?://[^\s\"'<>]+", text))
            except Exception:
                pass
    except Exception:
        pass
    # normalize + de-dupe
    return list(dict.fromkeys(_norm(u) for u in out))

# -----------------------------------------------------------------------------
# Asset hint extraction (iframe/src, absolute .pdf, JSON blocks)
# -----------------------------------------------------------------------------
def extract_pdf_hints(seed_html: str, base: str) -> list[str]:
    """Pull reasonable .pdf candidates directly from HTML before LLM."""
    hints = set()
    html = seed_html or ""

    # iframe/object/embed sources
    for m in re.finditer(r"""<(?:iframe|embed|object)[^>]+?(?:src|data)=["']([^"']+)["']""", html, re.I):
        hints.add(_norm(up.urljoin(base, m.group(1).strip())))

    # absolute .pdf anywhere in HTML (including content.*)
    for m in re.finditer(r"https?://[^\s\"'<>]+\.pdf(?:[?#][^\s\"']*)?", html, re.I):
        hints.add(_norm(m.group(0)))

    # JSON/Ld & app-state keys with URLs
    for m in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", html, re.I):
        block = (m.group(1) or "")
        for key in ("pdfUrl", "document", "file", "fileUrl", "url"):
            for mm in re.finditer(rf'"{key}"\s*:\s*"([^"]+\.pdf[^"]*)"', block, re.I):
                hints.add(_norm(mm.group(1)))

    # Heuristic: prefer same org or obvious pdfs
    out = []
    for u in hints:
        host = (up.urlsplit(u).hostname or "").lower()
        if u.lower().endswith(".pdf") or "content." in host or "cdn." in host:
            out.append(u)
    return list(dict.fromkeys(out))[:80]  # cap to keep prompt small


# -----------------------------------------------------------------------------
# Prompts (nudged for resource/viewer behavior)
# -----------------------------------------------------------------------------
PROMPT_SMALL = """You are a forms discovery agent extracting PDF form links.

EXAMPLES to INCLUDE:
✓ https://site.com/forms/application-benefits.pdf
✓ https://content.schwab.com/forms/SF-2826.pdf
✓ Forms with codes: SF-*, OPM-*, IRS Form *, Form W-*, etc.

EXAMPLES to EXCLUDE:
✗ annual-report.pdf, whitepaper.pdf, handbook.pdf

Return STRICT JSON:
{"pdf_urls":[ "https://...pdf", ... ], "paths":[ "/forms/", "/applications/", ... ], "expected_total": 50}

Rules:
- ONLY actionable forms: application, request, enrollment, authorization, consent, affidavit, declaration, appeal, designation, verification, waiver, election, claim
- EXCLUDE: report, handbook, guide, brochure, whitepaper, presentation, transcript, policy, publication, booklet
- If viewer page, extract embedded PDF URL
- "pdf_urls": absolute HTTPS ending .pdf
- "paths": promising directories to crawl
- "expected_total": YOUR BEST ESTIMATE of total forms available on this site (based on sitemap, page structure, etc.)
- Return up to 5000 candidates ranked by form probability
- JSON ONLY, no markdown, no commentary
"""

PROMPT_LARGE = """You are a forms discovery agent.
Return a strict JSON object only:
{"pdf_urls":[ "https://...pdf", ... ], "paths":[ "/forms/...", "/applications", ... ], "expected_total": 50}
Context samples:
- Seed page HTML excerpts (top/tail)
- A short list of likely form pages from sitemap
- Asset hints discovered in HTML (iframe/src or JSON with pdfUrl/file/document/url keys)

Rules:
- ONLY actionable PDFs: applications, requests, enrollment, authorizations, consents, affidavits, declarations, appeals, designations, verifications.
- Exclude marketing PDFs (reports, handbooks, brochures, whitepapers).
- Prefer content hosted on content.* or client.* subdomains when they serve the actual PDF (even with query strings).
- Treat viewer endpoints that load PDFs in <iframe>/<embed>/<object> as the actual PDF links.
- "pdf_urls": absolute https URLs ending .pdf on the same organization
- "paths": short same-site paths worth probing next
- "expected_total": YOUR BEST ESTIMATE of total forms available (based on sitemap size, page mentions, directory structure, etc.)
- No commentary—JSON only. Return up to 5000 best candidates.
"""

# -----------------------------------------------------------------------------
# Main entry
# -----------------------------------------------------------------------------
def discover(seed: str, seed_html: str, sitemap_urls: List[str]) -> Dict[str, Any]:
    """
    LLM-first discovery with fast sitemap hints + asset hints from HTML.
    Uses two-tier routing: Gemini Flash → Claude Sonnet fallback.

    Returns dict: { pdf_urls: [...], paths:[...], note?: str, error?: str }
    """
    seed = _norm(seed)
    host = up.urlsplit(seed).netloc or ""

    # 0) Quick harvest (non-blocking, immediate)
    # Prefer sitemap PDFs (often plenty on doc-heavy sites)
    def harvest_sitemap_quick(sitemap_urls: List[str], host: str) -> Dict[str, List[str]]:
        pdfs: List[str] = []
        pages: List[str] = []
        if "://" in host:
            host_root = (up.urlsplit(host).netloc or "").lower()
        else:
            host_root = (up.urlsplit("https://" + host).netloc or "").lower()
        for u in sitemap_urls or []:
            nu = _norm(u)
            h = (up.urlsplit(nu).netloc or "").lower()
            # allow org siblings (content/client.*)
            if host_root not in h:
                if not (host_root.split(".")[-2:] == h.split(".")[-2:]):
                    continue
            if PDF_RE.search(nu):
                pdfs.append(nu)
            else:
                path = up.urlsplit(nu).path or ""
                if re.search(r"(form|forms|application|request|enroll|enrollment|authorization|consent|affidavit|declaration|appeal|designation|verification)", path, re.I):
                    pages.append(nu)
        pdfs = list(dict.fromkeys(pdfs))[:5000]
        pages = list(dict.fromkeys(pages))[:1000]
        return {"pdfs": pdfs, "form_pages": pages}

    quick = harvest_sitemap_quick(sitemap_urls or [], host)
    pdfs_quick = quick["pdfs"]
    pages_quick = quick["form_pages"]

    ENOUGH = 50
    if len(pdfs_quick) >= ENOUGH:
        logger.info(f"Sitemap found {len(pdfs_quick)} PDFs, skipping LLM")
        return {"pdf_urls": pdfs_quick[:5000], "paths": [], "expected_total": len(pdfs_quick), "note": "sitemap_quick"}

    # Asset hints from seed HTML (pre-LLM)
    asset_hints = extract_pdf_hints(seed_html, seed)

    # 1) Small "scout" call (Tier 1: Gemini Flash)
    messages_small = [
        {"role": "system", "content": "You extract actionable PDF form links for the given site."},
        {"role": "user", "content": (
            PROMPT_SMALL
            + "\nSeed URL:\n" + seed
            + (("\n\nDiscovered asset hints (from HTML):\n" + "\n".join(asset_hints)) if asset_hints else "")
        )}
    ]

    # Context for two-tier routing
    context = {
        "task_type": "pdf_discovery",
        "data_size": "small",
        "sitemap_size": len(sitemap_urls) if sitemap_urls else 0
    }

    try:
        logger.info("LLM discovery (small): Calling Tier 1 (Gemini Flash)")
        raw_small = chat_complete(
            messages=messages_small,
            max_tokens=4000,  # Claude Opus limit is 4096, use 4000 for safety
            temperature=0.0,
            timeout=30.0,
            context=context,
            force_tier2=False  # Let it try Tier 1 first
        ).strip()
        logger.info("LLM discovery (small): Parsing results")

        # Use robust parser to handle JSON errors from LLM
        obj = parse_json_robust(raw_small)
        pdfs = [_norm(u) for u in (obj.get("pdf_urls") or []) if isinstance(u, str)]
        paths = [p.strip() for p in (obj.get("paths") or []) if isinstance(p, str)]
        expected_total = obj.get("expected_total") or len(pdfs)
        pdfs = [u for u in pdfs if u.lower().startswith("https://") and u.lower().endswith(".pdf")]
        logger.info(f"LLM discovery (small): Found {len(pdfs)} PDFs, {len(paths)} paths, expected ~{expected_total}")

        if len(pdfs) >= ENOUGH:
            logger.info(f"Sufficient PDFs found ({len(pdfs)}), returning early")
            return {"pdf_urls": list(dict.fromkeys(pdfs))[:5000], "paths": paths[:1000], "expected_total": expected_total, "note": "llm_small"}

        pdfs = list(dict.fromkeys(pdfs_quick + pdfs))
        logger.info(f"Combined total: {len(pdfs)} PDFs (sitemap + LLM)")

    except LLMError as e:
        logger.warning(f"LLM discovery (small) failed: {str(e)}")
        logger.info(f"Falling back to sitemap harvest ({len(pdfs_quick)} PDFs)")
        pdfs = pdfs_quick[:]
        paths = pages_quick[:500]
        expected_total = len(pdfs_quick) if pdfs_quick else 50

    # 2) Trimmed "large" pass (top + tail excerpts) if still not enough
    if len(pdfs) >= ENOUGH:
        logger.info(f"Already have {len(pdfs)} PDFs, skipping large LLM call")
        return {"pdf_urls": pdfs[:5000], "paths": paths[:1000], "expected_total": expected_total, "note": "small_sufficient"}

    top_excerpt = (seed_html or "")[:20000]
    tail_excerpt = (seed_html or "")[-20000:]
    site_sample = pages_quick[:80]

    messages_large = [
        {"role": "system", "content": "You extract actionable PDF form links for the given site."},
        {"role": "user", "content": (
            PROMPT_LARGE
            + "\n\nSeed URL:\n" + seed
            + "\n\nHTML excerpt (top):\n" + top_excerpt
            + "\n\nHTML excerpt (tail):\n" + tail_excerpt
            + (("\n\nLikely form pages (sample):\n" + "\n".join(site_sample)) if site_sample else "")
            + (("\n\nDiscovered asset hints (from HTML):\n" + "\n".join(asset_hints)) if asset_hints else "")
        )}
    ]

    # Update context for larger data
    context_large = {
        "task_type": "pdf_discovery",
        "data_size": "large",
        "sitemap_size": len(sitemap_urls) if sitemap_urls else 0,
        "html_size": len(seed_html) if seed_html else 0
    }

    try:
        logger.info("LLM discovery (large): Calling for deeper analysis")
        raw_large = chat_complete(
            messages=messages_large,
            max_tokens=4000,  # Claude Opus limit is 4096, use 4000 for safety
            temperature=0.0,
            timeout=60.0,
            context=context_large,
            force_tier2=False  # Still allow Tier 1, but more likely to fallback
        ).strip()
        logger.info("LLM discovery (large): Parsing results")

        # Use robust parser to handle JSON errors from LLM
        obj2 = parse_json_robust(raw_large)
        pdfs2 = [_norm(u) for u in (obj2.get("pdf_urls") or []) if isinstance(u, str)]
        paths2 = [p.strip() for p in (obj2.get("paths") or []) if isinstance(p, str)]
        expected_total2 = obj2.get("expected_total")
        pdfs2 = [u for u in pdfs2 if u.lower().startswith("https://") and u.lower().endswith(".pdf")]

        logger.info(f"LLM discovery (large): Found {len(pdfs2)} additional PDFs, {len(paths2)} paths")
        pdfs = list(dict.fromkeys(pdfs + pdfs2))
        paths_combined = list(dict.fromkeys(paths + paths2))
        final_expected = expected_total2 if expected_total2 else expected_total

        logger.info(f"Total discovered: {len(pdfs)} PDFs, {len(paths_combined)} paths, expected ~{final_expected}")
        return {"pdf_urls": pdfs[:5000], "paths": paths_combined[:1000], "expected_total": final_expected, "note": "llm_large"}

    except LLMError as e:
        logger.warning(f"LLM discovery (large) failed: {str(e)}")
        logger.info(f"Returning {len(pdfs)} PDFs, {len(pages_quick)} paths for HTML crawl")
        est_total = expected_total if 'expected_total' in locals() else max(len(pdfs), len(pdfs_quick))
        return {"pdf_urls": pdfs[:5000], "paths": pages_quick[:500], "expected_total": est_total, "note": "html_crawl_mode"}
