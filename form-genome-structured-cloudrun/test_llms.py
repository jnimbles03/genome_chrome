#!/usr/bin/env python3
"""
Direct LLM API test script to isolate issues
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

print("=" * 70)
print("DIRECT LLM API TESTING")
print("=" * 70)

# Test Gemini
print("\n📝 Test 1: Gemini Flash API")
print("-" * 70)
try:
    import google.generativeai as genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise Exception("GEMINI_API_KEY not found in environment")

    print(f"API Key: {api_key[:20]}...")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    test_prompt = """Extract the form title from this PDF text:

MEMBER APPLICATION FORM
Boeing Employees Credit Union

Name: _______________
Address: _____________

Return: Form Name"""

    print(f"\nPrompt: {test_prompt[:50]}...")
    response = model.generate_content(test_prompt)

    print(f"\n✅ Gemini Success!")
    print(f"Response type: {type(response)}")
    print(f"Response text: {response.text[:200] if response.text else 'EMPTY'}")

    # Check parts
    if hasattr(response, 'parts'):
        print(f"Parts count: {len(response.parts)}")
        for i, part in enumerate(response.parts):
            if hasattr(part, 'text'):
                print(f"  Part {i}: {part.text[:100]}")

except Exception as e:
    print(f"\n❌ Gemini Error: {str(e)}")
    import traceback
    traceback.print_exc()

# Test Anthropic
print("\n\n📝 Test 2: Anthropic Claude API")
print("-" * 70)
try:
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise Exception("ANTHROPIC_API_KEY not found in environment")

    print(f"API Key: {api_key[:20]}...")
    print(f"Anthropic package version: {anthropic.__version__}")

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-3-sonnet-20240229",
        max_tokens=100,
        messages=[{
            "role": "user",
            "content": "Extract the title from: Form W-2. Return only the title."
        }]
    )

    print(f"\n✅ Anthropic Success!")
    print(f"Response content: {response.content[0].text[:200]}")

except Exception as e:
    print(f"\n❌ Anthropic Error: {str(e)}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("TEST COMPLETE")
print("=" * 70)
