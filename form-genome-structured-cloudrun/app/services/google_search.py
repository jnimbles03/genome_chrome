# app/services/google_search.py
# Google Programmable Search (CSE) JSON API helper.
# Uses GOOGLE_API_KEY + GOOGLE_CSE_ID (cx) from environment.

import os
import requests

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID  = os.getenv("GOOGLE_CSE_ID")  # your cx value, e.g., c1ca87e7aa0484825
GOOGLE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"

def search_pdfs(domain: str, extra_terms: str = "forms", max_results: int = 200) -> list[str]:
    """
    Query CSE for: site:<domain> filetype:pdf <extra_terms>
    Returns a de-duplicated list of PDF URLs (up to max_results).
    """
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        raise RuntimeError("Set GOOGLE_API_KEY and GOOGLE_CSE_ID in environment")

    q = f"site:{domain} filetype:pdf {extra_terms}".strip()
    urls: list[str] = []
    start = 1  # CSE returns 10 per page, start ∈ {1, 11, 21, ...}
    while len(urls) < max_results and start <= 91:
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CSE_ID,
            "q": q,
            "num": 10,
            "start": start
        }
        r = requests.get(GOOGLE_ENDPOINT, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", []) or []
        if not items:
            break
        for it in items:
            link = (it.get("link") or "").strip()
            if link.lower().endswith(".pdf"):
                urls.append(link)
        start += 10

    # dedupe while preserving order
    seen, deduped = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
        if len(deduped) >= max_results:
            break
    return deduped
