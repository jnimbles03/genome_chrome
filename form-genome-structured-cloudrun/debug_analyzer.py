# debug_analyzer.py
# High-speed CLI for testing the Form Genome Analyzer
# Usage: python debug_analyzer.py <pdf_path> [--url <url>]

import os
import sys
import json
import logging
import argparse
from typing import Dict, Any

# Add project root to path so we can import app modules
sys.path.append(os.getcwd())

# Setup basic logging to console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("debug_analyzer")

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

def run_debug(pdf_path: str, url: str = None):
    """Run analysis and print detailed results."""
    from app.api.analyze import analyze_pdf_file
    
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        return

    print("\n" + "="*80)
    print(f"DEBUGGING ANALYZER")
    print(f"File: {pdf_path}")
    print(f"URL:  {url}")
    print("="*80)

    try:
        # Step 1: Run Stage 1 & 2
        results = analyze_pdf_file(pdf_path, os.path.basename(pdf_path), url=url)
        
        # Step 2: Display Results
        print("\n[V] ANALYSIS COMPLETE\n")
        print(f"Title:       {results.get('title')}")
        print(f"Entity:      {results.get('entity')}")
        print(f"Complexity:  {results.get('complexity')}/100 ({results.get('scoring_method')} Method)")
        print(f"Tier:        {'Claude Opus' if not results.get('gemini_analyzed') else 'Gemini Flash'}")
        
        if results.get('scoring_weights'):
            print(f"Weights:     {results.get('scoring_weights')}")
        
        print("\n--- Field Counts ---")
        print(f"Total Fields:    {results.get('total_field_count')}")
        print(f"Checkboxes:      {results.get('checkboxes')}")
        print(f"Text Fields:     {results.get('text_fields')}")
        print(f"Signatures:      {results.get('signature_analysis', {}).get('signature_count', 0)}")
        
        print("\n--- Requirements ---")
        print(f"Notarization:    {results.get('notarization_required')}")
        print(f"Attachments:     {results.get('attachments_required')}")
        print(f"Payment:         {results.get('payment_required')} ({results.get('payment_amount')})")
        print(f"ID Required:     {results.get('identification_required')}")
        print(f"Logic Clauses:   {results.get('conditional_logic')}")

        if not results.get('success'):
            print(f"\n[X] FAILURE: {results.get('error')}")

        # Dump raw JSON for piping
        print("\n" + "-"*40)
        print("RAW JSON RESULT:")
        print(json.dumps(results, indent=2))

    except Exception as e:
        logger.exception(f"Debug run failed: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Debug Form Genome Analyzer")
    parser.add_argument("pdf", help="Path to local PDF file")
    parser.add_argument("--url", help="Contextual URL for domain mapping", default=None)
    
    args = parser.parse_args()
    run_debug(args.pdf, args.url)
