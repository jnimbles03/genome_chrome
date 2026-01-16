
import os
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load env to get key
load_dotenv()

def test_gemini():
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    
    print(f"Testing Gemini Model: {model_name}")
    print(f"Key Present: {bool(api_key)}")
    
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents="Return the word 'SUCCESS' if you can hear me."
        )
        print(f"✅ Result: {response.text}")
    except Exception as e:
        print(f"❌ Failed: {e}")
        
    print("\nListing Available Models:")
    try:
        # Re-init client to be sure
        client = genai.Client(api_key=api_key)
        # List models
        pager = client.models.list(config={"page_size": 100})
        for m in pager:
            # Just print the name to be safe
            print(f"- {m.name} (Display: {m.display_name})")
    except Exception as e:
        print(f"Failed to list models: {e}")

if __name__ == "__main__":
    test_gemini()
