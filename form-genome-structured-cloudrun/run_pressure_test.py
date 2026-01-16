# run_pressure_test.py
import json
import requests
import time
import os

API_BASE = "http://127.0.0.1:8081/api"

def run_test():
    with open('pressure_test_urls.json', 'r') as f:
        verticals = json.load(f)
    
    all_urls = []
    for vertical, urls in verticals.items():
        all_urls.extend(urls)
    
    print(f"Starting pressure test for {len(all_urls)} forms across {len(verticals)} verticals...")
    
    # Send to batch endpoint
    try:
        resp = requests.post(f"{API_BASE}/batch/create", json={"urls": all_urls})
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
                
            time.sleep(10)
            
        print("\n[V] PRESSURE TEST COMPLETE")
        print(f"Final Count: {completed} successful, {failed} failed.")
        
    except Exception as e:
        print(f"Request failed: {str(e)}")

if __name__ == "__main__":
    run_test()
