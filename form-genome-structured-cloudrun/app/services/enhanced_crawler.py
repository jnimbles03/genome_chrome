import re
import time
import hashlib
from urllib.parse import urlparse, urljoin, parse_qs, urlencode
import xml.etree.ElementTree as ET

import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from app.services.remote_fetch import MAX_PDF_BYTES, open_stream, read_capped

__all__ = ["EnhancedWebCrawler"]

CRAWLER_MARKER = "full-2025-09-06"
CRAWLER_SHA = "0f"
import pathlib, hashlib as _hashlib
try:
    _p = pathlib.Path(__file__)
    CRAWLER_SHA = _hashlib.sha256(_p.read_bytes()).hexdigest()[:12]
except Exception:
    pass

TRACKER_PARAMS = ("utm_", "gclid", "fbclid", "mc_cid", "mc_eid", "trk")
RETRY_STATUSES = (429, 500, 502, 503, 504)

DEFAULT_CFG = dict(
    timeout=30,
    read_timeout=45,
    max_content=5_000_000,
    user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
    pdf_ctypes=("application/pdf", "application/x-pdf", "binary/octet-stream"),
    max_children_per_page=50,
    max_clicks_per_page=20,
    enable_playwright=True,
    max_seconds=90,
    per_host_delay=0.5,
)

def canonicalize(url: str) -> str:
    """Normalize URL for dedup: ensure scheme, strip trackers, stable URL-encoded query, trim trailing slash."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    q = {k: v for k, v in q.items() if not k.lower().startswith(TRACKER_PARAMS)}
    items = []
    for k in sorted(q):
        for val in sorted(q[k]):
            items.append((k, val))
    query = urlencode(items, doseq=True)
    clean = p._replace(query=query).geturl()
    if clean.endswith("/"):
        clean = clean[:-1]
    return clean

class EnhancedWebCrawler:
    def __init__(self, config: dict | None = None):
        self.config = {**DEFAULT_CFG, **(config or {})}

        # requests session with retries. Status-based retries are done in
        # remote_fetch.open_stream: urllib3 reads the whole error body into
        # memory before it retries a status or honors Retry-After.
        self.session = requests.Session()
        retry = Retry(
            total=3, connect=3, read=3, backoff_factor=0.5,
            allowed_methods=False,
            respect_retry_after_header=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=32)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "User-Agent": self.config["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

        self.seen_pages: set[str] = set()
        self.found_pdfs: dict[str, dict] = {}
        self.deadline = time.monotonic() + float(self.config.get("max_seconds", 90))
        self.next_ok: dict[str, float] = {}  # host -> earliest next request time

    def _too_late(self) -> bool:
        return time.monotonic() >= self.deadline

    def _open(self, url: str) -> requests.Response:
        return open_stream(
            url,
            session=self.session,
            timeout=(self.config["timeout"], self.config["read_timeout"]),
            retry_statuses=RETRY_STATUSES,
            retries=3,
        )

    def _get(self, url: str, max_bytes: int, truncate: bool = False) -> requests.Response:
        resp = self._open(url)
        read_capped(resp, max_bytes, truncate=truncate)
        return resp

    def _wait_per_host(self, host: str):
        t = self.next_ok.get(host, 0.0)
        now = time.monotonic()
        if now < t:
            time.sleep(t - now)
        self.next_ok[host] = max(now, t) + float(self.config.get("per_host_delay", 0.5))

    def crawl_site(self, start_url: str, max_depth: int = 3, max_pages: int = 200):
        start_url = canonicalize(start_url)
        sitemaps = self._fetch_robots_and_sitemaps(start_url)
        seeds = self._seed_from_sitemaps(sitemaps)
        frontier = list(dict.fromkeys([start_url] + seeds))
        for seed in frontier:
            if self._too_late(): break
            if len(self.seen_pages) >= max_pages: break
            self._crawl_recursive(seed, 0, max_depth, max_pages)
        out = list(self.found_pdfs.values())
        out.sort(key=lambda x: (-x.get("score", 0), x["url"]))
        # ensure we return within budget
        if self._too_late():
            return out
        return out

    def _crawl_recursive(self, url: str, depth: int, max_depth: int, max_pages: int):
        if self._too_late():
            return
        url = canonicalize(url)
        if url in self.seen_pages or depth > max_depth or len(self.seen_pages) >= max_pages:
            return
        self.seen_pages.add(url)

        host = urlparse(url).netloc
        self._wait_per_host(host)
        try:
            r = self._open(url)
            ctype = (r.headers.get("content-type") or "").lower()
            if "pdf" in ctype:
                self._record_pdf(url, read_capped(r, MAX_PDF_BYTES), ctype, referrer=None)
                return
            if not r.ok or "text/html" not in ctype:
                r.close()
                return
            read_capped(r, self.config["max_content"], truncate=True)
        except requests.RequestException as e:
            print(f"[crawler] GET failed {url}: {e}")
            return

        html = r.text[: self.config["max_content"]]
        soup = BeautifulSoup(html, "lxml")

        links = [urljoin(url, a.get("href")) for a in soup.find_all("a", href=True)]
        for el in soup.select("a,button"):
            label = el.get_text(" ", strip=True).lower()
            if label and re.search(r"(download|form|application|pdf|print|document|certificate)", label):
                href = el.get("href")
                if href:
                    links.append(urljoin(url, href))
        # explicit .pdf anchors
        links.extend([urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
                      if a["href"].lower().endswith(".pdf")])

        clean: list[str] = []
        for L in links:
            if not L:
                continue
            Lc = canonicalize(L)
            if Lc in self.seen_pages:
                continue
            if Lc.lower().startswith("javascript:"):
                continue
            if re.search(r"^(javascript:|mailto:)", Lc, re.I):
                continue
            clean.append(Lc)

        print(f"[crawler] Found {len(clean)} candidate links on {url}")

        # PRIORITY: Run Playwright FIRST to extract embedded PDFs (e.g., Schwab forms)
        # This ensures we harvest JavaScript-rendered content before exhausting time budget
        if self.config.get("enable_playwright", True) and not self._too_late():
            print(f"[crawler] Running Playwright harvest EARLY (before HEAD/GET loop)")
            self._harvest_via_playwright(url)
        else:
            print(f"[crawler] Playwright disabled for {url}")

        # THEN do HEAD/GET checks on candidate links if time remains
        for L in clean:
            if self._too_late():
                break
            try:
                hostL = urlparse(L).netloc
                self._wait_per_host(hostL)
                h = self.session.head(L, allow_redirects=True,
                                      timeout=(self.config["timeout"], self.config["read_timeout"]))
                hct = (h.headers.get("content-type") or "").lower()
                hcd = (h.headers.get("content-disposition") or "").lower()
                looks_like_pdf = (any(ct in hct for ct in self.config["pdf_ctypes"])
                                  or L.lower().endswith(".pdf")
                                  or (("attachment" in hcd or "filename=" in hcd) and ".pdf" in hcd))
                if looks_like_pdf:
                    g = self._open(h.url)
                    if g.ok:
                        self._record_pdf(h.url, read_capped(g, MAX_PDF_BYTES), hct, referrer=url)
                    else:
                        g.close()
            except requests.RequestException as e:
                print(f"[crawler] HEAD/GET failed {L}: {e}")
                continue

        base_host = urlparse(url).netloc
        children: list[tuple[int, str]] = []
        for L in clean:
            p = urlparse(L)
            score = 0
            if p.netloc == base_host:
                score += 2
            if re.search(r"(forms?|document|library|resources?|downloads?|media)", p.path, re.I):
                score += 3
            if "pdf" in L.lower():
                score += 1
            if re.search(r"/documents-and-forms/approved-forms", p.path, re.I):
                score += 4
            if re.search(r"/forms?/", p.path, re.I):
                score += 2
            if score >= 2:
                children.append((score, L))
        children.sort(reverse=True)
        for _, child in children[: int(self.config.get("max_children_per_page", 50))]:
            if self._too_late():
                break
            self._crawl_recursive(child, depth + 1, max_depth, max_pages)

    def _harvest_via_playwright(self, page_url: str):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-software-rasterizer"],
                )
                ctx = browser.new_context(
                    user_agent=self.session.headers.get("User-Agent", DEFAULT_CFG["user_agent"]),
                    ignore_https_errors=True,
                )
                page = ctx.new_page()
                pdf_hits = []

                def on_response(resp):
                    headers = resp.headers or {}
                    ctype = (headers.get("content-type") or "").lower()
                    cdisp = (headers.get("content-disposition") or "").lower()
                    looks_like_pdf = ("pdf" in ctype or (("attachment" in cdisp or "filename=" in cdisp) and ".pdf" in cdisp))
                    if looks_like_pdf:
                        pdf_hits.append(resp)

                page.on("response", on_response)
                page.goto(page_url, wait_until="domcontentloaded", timeout=self.config["timeout"] * 1000)

                def safe_click(locator, nth=0, timeout=1200):
                    try:
                        if locator.nth(nth).is_visible():
                            locator.nth(nth).click(timeout=timeout)
                            page.wait_for_timeout(350)
                    except Exception:
                        pass

                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(300)
                except Exception:
                    pass

                candidates = page.locator(
                    "a:has-text('Download'), a:has-text('Form'), a:has-text('PDF'), "
                    "button:has-text('Download'), button:has-text('Form'), button:has-text('PDF'), "
                    "a[download], a[href$=\".pdf\"]"
                )
                print(f"[crawler] Found {candidates.count()} click candidates on {page_url}")
                for i in range(min(candidates.count(), int(self.config.get("max_clicks_per_page", 20)))):
                    if self._too_late(): break
                    safe_click(candidates, nth=i)

                for sel in ("[aria-expanded='false']", ".accordion button", ".accordion-toggle", ".accordion-button", ".accordion-header button"):
                    if self._too_late(): break
                    safe_click(page.locator(sel), nth=0, timeout=800)
                    page.wait_for_timeout(1000)
                    try:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    except Exception:
                        pass

                page.wait_for_timeout(1500)

                # Direct extraction and fetch of PDF links
                hrefs = page.evaluate("""
                    () => {
                        const nodes = document.querySelectorAll("a[href$='.pdf'], a[download]");
                        return Array.from(nodes).map(n => n.href);
                    }
                """) or []
                for href in hrefs[:100]:  # cap to avoid overload
                    try:
                        r = self._open(href)
                        if r.ok and 'pdf' in r.headers.get('content-type', '').lower():
                            self._record_pdf(href, read_capped(r, MAX_PDF_BYTES), r.headers.get('content-type', '').lower(), referrer=page_url)
                        else:
                            r.close()
                    except Exception as e:
                        print(f"[crawler] direct fetch failed {href}: {e}")
                        continue

                for resp in pdf_hits:
                    try:
                        url = resp.url
                        hdrs = resp.headers or {}
                        ctype = (hdrs.get("content-type") or "").lower()
                        declared = (hdrs.get("content-length") or "").strip()
                        if (declared.isdigit() and int(declared) <= MAX_PDF_BYTES
                                and not hdrs.get("content-encoding")):
                            body = resp.body()
                        else:
                            body = self._get(url, MAX_PDF_BYTES).content
                        self._record_pdf(url, body, ctype, referrer=page_url)
                    except Exception:
                        continue
        except Exception as e:
            print(f"[crawler] Playwright error on {page_url}: {e}")
            return
        finally:
            try:
                ctx.close()
                browser.close()
            except Exception:
                pass

    def _fetch_robots_and_sitemaps(self, start_url: str):
        base = f"{urlparse(start_url).scheme}://{urlparse(start_url).netloc}"
        sitemaps: list[str] = []
        try:
            robots = urljoin(base, "/robots.txt")
            r = self._get(robots, self.config["max_content"], truncate=True)
            if r.ok:
                for line in r.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sitemaps.append(line.split(":", 1)[1].strip())
        except requests.RequestException:
            pass
        for guess in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
            try:
                u = urljoin(base, guess)
                g = self._get(u, self.config["max_content"])
                if g.ok and ("<urlset" in g.text or "<sitemapindex" in g.text):
                    sitemaps.append(u)
            except requests.RequestException:
                pass
        return list(dict.fromkeys(sitemaps))

    def _seed_from_sitemaps(self, sitemaps):
        seeds: list[str] = []
        for sm in sitemaps:
            try:
                r = self._get(sm, self.config["max_content"])
                if not r.ok:
                    continue
                try:
                    root = ET.fromstring(r.text)
                except ET.ParseError:
                    print(f"[crawler] sitemap parse error {sm}")
                    continue
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                for loc in root.findall(".//sm:loc", ns):
                    u = (loc.text or "").strip()
                    if re.search(r"(form|forms|document|download|pdf|resources|library|application)", u, re.I):
                        seeds.append(u)
            except Exception as e:
                print(f"[crawler] sitemap fetch error {sm}: {e}")
                continue
        return list(dict.fromkeys(seeds))

    def _record_pdf(self, url: str, content: bytes, ctype: str, referrer: str | None):
        url = canonicalize(url)
        if url in self.found_pdfs:
            print(f"[crawler] PDF already seen: {url}")
            return
        score = 0
        if url.lower().endswith(".pdf"):
            score += 2
        if re.search(r"(form|application|request|permit|certificate|intake|enrollment|claim)", url, re.I):
            score += 4
        if "download" in url.lower() or "document" in url.lower():
            score += 1
        sha = hashlib.sha256(content).hexdigest()
        print(f"[crawler] PDF: {url} size={len(content)} score={score} ref={referrer}")
        self.found_pdfs[url] = dict(
            url=url, referrer=referrer, content_sha256=sha,
            content_type=ctype, score=score, size=len(content),
        )