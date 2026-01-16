# run_custom_batch.py
import json
import requests
import time
import argparse
import os
from dotenv import load_dotenv

load_dotenv()

# Use custom port if set, otherwise default to 8080
PORT = os.getenv('FLASK_PORT', '8080')
API_BASE = f"http://127.0.0.1:{PORT}/api"

def run_custom_batch(urls):
    if not urls:
        print("Error: No URLs provided.")
        return
    
    print(f"Starting custom batch for {len(urls)} forms...")
    
    # Send to batch endpoint
    try:
        resp = requests.post(f"{API_BASE}/batch/create", json={"urls": urls})
        if resp.status_code not in [200, 201]:
            print(f"Error starting batch: {resp.text}")
            return
        
        job_id = resp.json().get('batch_id')
        print(f"Batch job started! Job ID: {job_id}")
        
        # Poll for completion
        while True:
            status_resp = requests.get(f"{API_BASE}/batch/status/{job_id}")
            if status_resp.status_code != 200:
                print(f"Error checking status: {status_resp.text}")
                break

            status_data = status_resp.json().get('batch', {})
            
            progress = status_data.get('progress', 0)
            status = status_data.get('status', 'processing')
            completed = status_data.get('completed_jobs', 0)
            total = status_data.get('total_jobs', 0)
            failed = status_data.get('failed_jobs', 0)
            
            print(f"[{status.upper()}] Progress: {progress}% ({completed}/{total} completed, {failed} failed)")
            
            if status in ['completed', 'failed']:
                break
                
            time.sleep(5)
            
        print("\n[V] CUSTOM BATCH COMPLETE")
        print(f"Final Count: {completed} successful, {failed} failed.")
        print(f"View results at: http://127.0.0.1:{PORT}/")
        
    except Exception as e:
        print(f"Request failed: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a custom batch of form URLs")
    parser.add_argument('urls', nargs='*', help="List of PDF URLs to analyze")
    parser.add_argument('--file', help="Path to a text file containing one URL per line")
    
    args = parser.parse_args()
    
    target_urls = args.urls
    if args.file:
        if os.path.exists(args.file):
            with open(args.file, 'r') as f:
                target_urls.extend([line.strip() for line in f if line.strip()])
        else:
            print(f"Error: File not found: {args.file}")
    
    if not target_urls:
        print("Please provide URLs as arguments or via --file.")
    else:
        run_custom_batch(target_urls)
