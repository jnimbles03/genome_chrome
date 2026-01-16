# app/services/hybrid_discover.py
# Hybrid Form Discovery - 4-Tier Cascade System
# Tier 1: Google CSE (if available) - 3-5 seconds
# Tier 2: Sitemap Parsing - 10-30 seconds
# Tier 3: Web Crawler (BeautifulSoup + Playwright) - 90 seconds
# Tier 4: LLM Discovery (fallback) - 2-4 minutes

import os
import logging
from urllib.parse import urlparse
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

def discover_forms(url: str, max_forms: int = 200, config: Optional[Dict] = None) -> List[str]:
    """
    Hybrid form discovery using cascading methods.

    Tries methods in order until sufficient forms are found:
    1. Google Custom Search (if API key available)
    2. Sitemap parsing (robots.txt + XML sitemaps)
    3. Web crawler (BeautifulSoup + Playwright)
    4. LLM discovery (last resort)

    Args:
        url: Starting URL or domain to discover forms from
        max_forms: Maximum number of forms to discover
        config: Optional configuration overrides

    Returns:
        List of PDF URLs discovered
    """
    config = config or {}
    pdf_urls = []
    methods_used = []
    domain = urlparse(url).netloc

    # Tier 1: Try Google CSE first (FASTEST - 3-5s)
    if os.getenv("GOOGLE_API_KEY") and os.getenv("GOOGLE_CSE_ID"):
        try:
            logger.info(f"🔍 Tier 1: Trying Google CSE for {domain}")
            from app.services import google_search

            # Extract domain-specific search terms
            extra_terms = "forms"
            if "irs.gov" in domain:
                extra_terms = "forms instructions"
            elif any(x in domain for x in ["credit", "bank", "financial"]):
                extra_terms = "forms applications"

            cse_results = google_search.search_pdfs(domain, extra_terms, max_results=max_forms)

            if cse_results and len(cse_results) >= min(10, max_forms // 2):
                pdf_urls.extend(cse_results[:max_forms])
                methods_used.append("google_cse")
                logger.info(f"✅ Tier 1: Google CSE found {len(cse_results)} forms in ~3-5s")
                return list(dict.fromkeys(pdf_urls))  # Dedupe
        except Exception as e:
            logger.warning(f"⚠️  Tier 1: Google CSE failed: {str(e)[:100]}")
    else:
        logger.info("⏭️  Tier 1: Google CSE skipped (no API key)")

    # Tier 2: Try Sitemap Parsing (FAST - 10-30s, FREE)
    if len(pdf_urls) < max_forms:
        try:
            logger.info(f"🗺️  Tier 2: Trying sitemap parsing for {url}")
            from app.services.enhanced_crawler import EnhancedWebCrawler

            crawler = EnhancedWebCrawler(config={"enable_playwright": False, "max_seconds": 30})
            sitemaps = crawler._fetch_robots_and_sitemaps(url)
            sitemap_urls = crawler._seed_from_sitemaps(sitemaps)

            # Filter for likely PDF forms
            pdf_candidates = [u for u in sitemap_urls if u.lower().endswith('.pdf')]

            if pdf_candidates:
                pdf_urls.extend(pdf_candidates[:max_forms - len(pdf_urls)])
                methods_used.append("sitemap")
                logger.info(f"✅ Tier 2: Sitemap found {len(pdf_candidates)} forms in ~10-30s")

                if len(pdf_urls) >= max_forms:
                    return list(dict.fromkeys(pdf_urls))
        except Exception as e:
            logger.warning(f"⚠️  Tier 2: Sitemap parsing failed: {str(e)[:100]}")

    # Tier 3: Try Web Crawler (THOROUGH - 90s)
    if len(pdf_urls) < max_forms:
        try:
            logger.info(f"🕷️  Tier 3: Trying web crawler for {url}")
            from app.services.enhanced_crawler import EnhancedWebCrawler

            crawler_config = {
                "enable_playwright": config.get("enable_playwright", True),
                "max_seconds": config.get("crawler_timeout", 90),
                "max_children_per_page": 50,
                "max_clicks_per_page": 20,
            }

            crawler = EnhancedWebCrawler(config=crawler_config)
            results = crawler.crawl_site(url, max_depth=3, max_pages=200)

            crawler_pdfs = [r["url"] for r in results]

            if crawler_pdfs:
                pdf_urls.extend(crawler_pdfs[:max_forms - len(pdf_urls)])
                methods_used.append("crawler")
                logger.info(f"✅ Tier 3: Web crawler found {len(crawler_pdfs)} forms in ~90s")

                if len(pdf_urls) >= max_forms:
                    return list(dict.fromkeys(pdf_urls))
        except Exception as e:
            logger.warning(f"⚠️  Tier 3: Web crawler failed: {str(e)[:200]}")

    # Tier 4: Try LLM Discovery (LAST RESORT - 2-4 minutes)
    if len(pdf_urls) < max_forms and config.get("llm_timeout", 0) > 0:
        try:
            logger.info(f"🤖 Tier 4: Trying LLM discovery for {url}")
            from app.services import llm_discover

            # Fetch HTML and sitemap for LLM discovery
            status_code, html, headers = llm_discover.fetch(url)
            if status_code == 200:
                sitemap_urls = llm_discover.try_sitemap(url)
                discovery_result = llm_discover.discover(url, html, sitemap_urls)

                if 'pdf_urls' in discovery_result:
                    llm_results = discovery_result['pdf_urls'][:max_forms - len(pdf_urls)]
                    if llm_results:
                        pdf_urls.extend(llm_results)
                        methods_used.append("llm")
                        logger.info(f"✅ Tier 4: LLM found {len(llm_results)} forms in ~2-4min")
        except Exception as e:
            logger.error(f"❌ Tier 4: LLM discovery failed: {str(e)[:200]}")

    # Dedupe and log final results
    final_urls = list(dict.fromkeys(pdf_urls))[:max_forms]

    logger.info(
        f"🎯 Hybrid Discovery Complete: {len(final_urls)} forms found "
        f"using methods: {', '.join(methods_used) or 'none'}"
    )

    return final_urls


def discover_forms_smart(url: str, max_forms: int = 200, strategy: str = "auto") -> List[str]:
    """
    Smart discovery with strategy selection.

    Strategies:
    - "fast": Google CSE + Sitemap only (< 30s)
    - "thorough": All tiers including crawler (< 2min)
    - "comprehensive": All tiers including LLM (< 5min)
    - "auto": Automatically choose based on domain/context

    Args:
        url: Starting URL
        max_forms: Max forms to discover
        strategy: Discovery strategy

    Returns:
        List of PDF URLs
    """
    if strategy == "fast":
        config = {"enable_playwright": False, "crawler_timeout": 0, "llm_timeout": 0}
    elif strategy == "thorough":
        config = {"enable_playwright": True, "crawler_timeout": 90, "llm_timeout": 0}
    elif strategy == "comprehensive":
        config = {"enable_playwright": True, "crawler_timeout": 90, "llm_timeout": 180}
    else:  # auto
        # Choose strategy based on domain characteristics
        domain = urlparse(url).netloc

        # Government sites: thorough (they have complex structures)
        if any(x in domain for x in [".gov", ".mil"]):
            config = {"enable_playwright": True, "crawler_timeout": 120, "llm_timeout": 0}
            logger.info("🏛️  Auto-selected 'thorough' strategy for government site")

        # Financial sites: balanced (enable crawler + Playwright, skip LLM)
        # Note: Financial sites often have many links, so we need more time
        elif any(x in domain for x in ["bank", "credit", "fidelity", "schwab", "vanguard"]):
            config = {"enable_playwright": True, "crawler_timeout": 120, "llm_timeout": 0}
            logger.info("💰 Auto-selected 'balanced' strategy for financial site")

        # Default: balanced
        else:
            config = {"enable_playwright": True, "crawler_timeout": 60, "llm_timeout": 0}
            logger.info("⚖️  Auto-selected 'balanced' strategy")

    return discover_forms(url, max_forms, config)
