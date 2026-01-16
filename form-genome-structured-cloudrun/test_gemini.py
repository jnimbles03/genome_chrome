
import os
import google.generativeai as genai_old
from google import genai
from google.genai import types

api_key = "AIzaSyDlCklqbmI6MKayoufnf36GpNzp67swbkA"

print("-" * 50)
print("Testing google-generativeai (Old SDK)...")
try:
    genai_old.configure(api_key=api_key)
    print("Listing models...")
    for m in genai_old.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f" - {m.name}")
    
    print("\nTesting generation with 'gemini-1.5-flash'...")
    model = genai_old.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content("Hello")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Old SDK Error: {e}")

print("\n" + "-" * 50)
print("Testing google-genai (New SDK)...")
try:
    client = genai.Client(api_key=api_key)
    
    print("Testing generation with 'gemini-1.5-flash'...")
    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents='Hello',
        config=types.GenerateContentConfig(max_output_tokens=10)
    )
    print(f"Response: {response.text}")

    print("\nTesting generation with 'gemini-1.5-flash-001'...")
    response = client.models.generate_content(
        model='gemini-1.5-flash-001',
        contents='Hello',
        config=types.GenerateContentConfig(max_output_tokens=10)
    )
    print(f"Response: {response.text}")

except Exception as e:
    print(f"New SDK Error: {e}")
