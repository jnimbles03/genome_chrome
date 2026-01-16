# app/services/bing_search.py
import os, requests

BING_ENDPOINT = os.getenv("BING_ENDPOINT", "https://api.bing.microsoft.com/v7.0/search")
BING_KEY = os.getenv("BING_KEY")  # set in your env

def search_pdfs(domain: str, extra_terms: str = "forms", count: int = 50):
    """
    Run a Bing query like: site:penfed.org filetype:pdf forms
    Returns a list of PDF URLs.
    """
    if not BING_KEY:
        raise RuntimeError("BING_KEY not set in env")

    q = f"site:{domain} filetype:pdf {extra_terms}"

    headers = {"Ocp-Apim-Subscription-Key": BING_KEY}
    params = {"q": q, "count": count}

    r = requests.get(BING_ENDPOINT, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    urls = []
    for w in data.get("webPages", {}).get("value", []):
        u = w.get("url")
        if u and u.lower().endswith(".pdf"):
            urls.append(u)
    return list(dict.fromkeys(urls))  # dedupe, preserve order
