#!/usr/bin/env python3
"""
Test script for hybrid form discovery system
Tests 4-tier cascade: Google CSE → Sitemap → Crawler → LLM
"""
import os
import sys
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add app to path
sys.path.insert(0, os.path.dirname(__file__))

from app.services.hybrid_discover import discover_forms_smart

print("=" * 80)
print("HYBRID FORM DISCOVERY TEST")
print("=" * 80)

# Test cases
test_cases = [
    {
        "name": "Schwab (Financial - Fast Strategy)",
        "url": "https://www.schwab.com/forms-and-applications",
        "max_forms": 20,
        "strategy": "auto",  # Should auto-select "fast" strategy
        "expected_methods": ["google_cse", "sitemap"],  # Should NOT need crawler or LLM
    },
    {
        "name": "IRS (Government - Thorough Strategy)",
        "url": "https://www.irs.gov/forms-instructions",
        "max_forms": 10,
        "strategy": "auto",  # Should auto-select "thorough" strategy
        "expected_methods": ["google_cse", "sitemap", "crawler"],
    },
]

results = []

for test in test_cases:
    print(f"\n{'=' * 80}")
    print(f"TEST: {test['name']}")
    print(f"URL: {test['url']}")
    print(f"Max Forms: {test['max_forms']}")
    print(f"Strategy: {test['strategy']}")
    print(f"{'=' * 80}\n")

    start_time = time.time()

    try:
        # Run hybrid discovery
        pdf_urls = discover_forms_smart(
            url=test['url'],
            max_forms=test['max_forms'],
            strategy=test['strategy']
        )

        elapsed = time.time() - start_time

        print(f"\n✅ SUCCESS!")
        print(f"   Forms Found: {len(pdf_urls)}")
        print(f"   Time: {elapsed:.2f}s")
        print(f"\n   Sample URLs:")
        for i, url in enumerate(pdf_urls[:5], 1):
            print(f"     {i}. {url[:80]}...")

        if len(pdf_urls) > 5:
            print(f"     ... and {len(pdf_urls) - 5} more")

        results.append({
            "name": test['name'],
            "success": True,
            "forms_found": len(pdf_urls),
            "elapsed": elapsed,
            "urls": pdf_urls
        })

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n❌ FAILED!")
        print(f"   Error: {str(e)}")
        print(f"   Time: {elapsed:.2f}s")

        results.append({
            "name": test['name'],
            "success": False,
            "error": str(e),
            "elapsed": elapsed
        })

# Summary
print(f"\n{'=' * 80}")
print("SUMMARY")
print(f"{'=' * 80}\n")

for result in results:
    status = "✅ PASS" if result['success'] else "❌ FAIL"
    print(f"{status} - {result['name']}")
    if result['success']:
        print(f"         {result['forms_found']} forms in {result['elapsed']:.2f}s")
    else:
        print(f"         Error: {result['error'][:50]}")

print(f"\n{'=' * 80}")

# Check if we improved over LLM-only (which found 1 form for Schwab)
schwab_result = next((r for r in results if "Schwab" in r['name']), None)
if schwab_result and schwab_result['success']:
    improvement = schwab_result['forms_found'] / 1  # LLM-only found 1
    print(f"\n🎯 IMPROVEMENT: {improvement:.1f}x more forms than LLM-only approach")
    print(f"   LLM-only: 1 form in ~2-4 minutes")
    print(f"   Hybrid:   {schwab_result['forms_found']} forms in {schwab_result['elapsed']:.1f}s")
