
import os
import google.generativeai as genai_old
from google import genai
from google.genai import types

api_key = "AIzaSyDlCklqbmI6MKayoufnf36GpNzp67swbkA"

print("-" * 50)
print("Testing google-genai (New SDK) with gemini-2.5-flash...")
try:
    client = genai.Client(api_key=api_key)
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents='Hello',
        config=types.GenerateContentConfig(max_output_tokens=10)
    )
    print(f"Response: {response.text}")
    print("SUCCESS with gemini-2.5-flash")

except Exception as e:
    print(f"New SDK Error with gemini-2.5-flash: {e}")
