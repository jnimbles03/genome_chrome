#!/usr/bin/env python3
"""
Test script for the analyze API endpoint.
Tests file upload and analysis functionality.
"""

import os
import sys
import requests
import tempfile

API_BASE = "http://localhost:8080"
AUTH_HEADERS = {"Authorization": f"Bearer {os.getenv('FORMGENOME_API_TOKEN', '')}"}

def test_health():
    """Test the health endpoint"""
    print("Testing /api/analyze/health...")
    response = requests.get(f"{API_BASE}/api/analyze/health")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    print()
    return response.status_code == 200

def test_txt_upload():
    """Test uploading a text file"""
    print("Testing TXT file upload...")

    # Create a temporary text file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write("This is a test form.\n")
        f.write("Name: _________________\n")
        f.write("Address: ______________\n")
        f.write("Phone: ________________\n")
        temp_path = f.name

    try:
        with open(temp_path, 'rb') as f:
            files = {'file': ('test_form.txt', f, 'text/plain')}
            response = requests.post(f"{API_BASE}/api/analyze/upload", files=files, headers=AUTH_HEADERS)

        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Success: {result.get('success')}")

        if result.get('success'):
            results = result.get('results', {})
            print(f"File Type: {results.get('fileType')}")
            print(f"Lines: {results.get('lines')}")
            print(f"Words: {results.get('words')}")
            print(f"Characters: {results.get('characters')}")
        else:
            print(f"Error: {result.get('error')}")

        print()
        return response.status_code == 200 and result.get('success')

    finally:
        os.unlink(temp_path)

def test_csv_upload():
    """Test uploading a CSV file"""
    print("Testing CSV file upload...")

    # Create a temporary CSV file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("Name,Email,Phone\n")
        f.write("John Doe,john@example.com,555-1234\n")
        f.write("Jane Smith,jane@example.com,555-5678\n")
        temp_path = f.name

    try:
        with open(temp_path, 'rb') as f:
            files = {'file': ('test_data.csv', f, 'text/csv')}
            response = requests.post(f"{API_BASE}/api/analyze/upload", files=files, headers=AUTH_HEADERS)

        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Success: {result.get('success')}")

        if result.get('success'):
            results = result.get('results', {})
            print(f"File Type: {results.get('fileType')}")
            print(f"Columns: {results.get('columns')}")
            print(f"Rows: {results.get('rows')}")
            print(f"Headers: {results.get('headers')}")
        else:
            print(f"Error: {result.get('error')}")

        print()
        return response.status_code == 200 and result.get('success')

    finally:
        os.unlink(temp_path)

def test_invalid_file_type():
    """Test uploading an invalid file type"""
    print("Testing invalid file type...")

    # Create a temporary file with invalid extension
    with tempfile.NamedTemporaryFile(mode='w', suffix='.exe', delete=False) as f:
        f.write("Invalid file")
        temp_path = f.name

    try:
        with open(temp_path, 'rb') as f:
            files = {'file': ('malware.exe', f, 'application/octet-stream')}
            response = requests.post(f"{API_BASE}/api/analyze/upload", files=files, headers=AUTH_HEADERS)

        print(f"Status: {response.status_code}")
        result = response.json()
        print(f"Success: {result.get('success')}")
        print(f"Error: {result.get('error')}")
        print()

        # Should fail with 400
        return response.status_code == 400 and not result.get('success')

    finally:
        os.unlink(temp_path)

def main():
    """Run all tests"""
    print("=" * 60)
    print("Analyze API Test Suite")
    print("=" * 60)
    print()

    # Check if server is running
    try:
        response = requests.get(f"{API_BASE}/health", timeout=2)
        print(f"✅ Server is running at {API_BASE}")
        print()
    except requests.exceptions.RequestException:
        print(f"❌ Server is not running at {API_BASE}")
        print("   Please start the server with: python3 main.py")
        sys.exit(1)

    # Run tests
    results = []

    results.append(("Health Check", test_health()))
    results.append(("TXT Upload", test_txt_upload()))
    results.append(("CSV Upload", test_csv_upload()))
    results.append(("Invalid File Type", test_invalid_file_type()))

    # Print summary
    print("=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = 0
    failed = 0

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {name}")
        if result:
            passed += 1
        else:
            failed += 1

    print()
    print(f"Passed: {passed}/{len(results)}")
    print(f"Failed: {failed}/{len(results)}")
    print()

    if failed == 0:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed. Check output above for details.")
        return 1

if __name__ == '__main__':
    sys.exit(main())
