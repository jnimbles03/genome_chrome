
import os
import sys
from dotenv import load_dotenv

# Load env
load_dotenv()

# Add app to path
sys.path.append(os.getcwd())

from app.services.llm_router import _call_anthropic
from app.core.config import Config

def test_model(model_name):
    print(f"\nTesting model: {model_name}...")
    try:
        response = _call_anthropic(
            model=model_name,
            messages=[{"role": "user", "content": "Return the word 'SUCCESS' if you can hear me."}],
            max_tokens=10,
            temperature=0,
            timeout=10,
            api_key=Config.ANTHROPIC_API_KEY
        )
        print(f"✅ Result: {response}")
        return True
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False

if __name__ == "__main__":
    print(f"API Key present: {bool(Config.ANTHROPIC_API_KEY)}")
    
    # 1. Test the old model (expect failure)
    # old_model = "claude-3-5-sonnet-20240620"
    # test_model(old_model)
    
    # 2. Test aliases
    # test_model("claude-3-5-sonnet-latest")
    
    # 3. Test Opus fallback
    test_model("claude-3-opus-20240229")
