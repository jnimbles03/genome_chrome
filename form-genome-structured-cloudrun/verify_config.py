import sys
import os

# Add parent directory to path so we can import 'app'
sys.path.append(os.getcwd())

from app.services.llm_router import LLMConfig
from app.core.config import Config

print("="*60)
print("VERIFYING CONFIGURATION INTEGRATION")
print("="*60)

# Check Config directly
print(f"[Config Class] ANTHROPIC_MODEL: {Config.ANTHROPIC_MODEL}")

# Check LLMConfig (which router uses)
llm_config = LLMConfig.from_env()
print(f"[LLMConfig]    anthropic_model: {llm_config.anthropic_model}")

if llm_config.anthropic_model == "claude-3-opus-20240229":
    print("\n✅ SUCCESS: Model is correctly set to Opus (Fallback stability).")
    exit(0)
elif llm_config.anthropic_model == "claude-3-5-sonnet-20241022":
    print("\n✅ SUCCESS: Model is correctly set to Sonnet 3.5 (20241022).")
    exit(0)
else:
    print(f"\n❌ FAILURE: Model is set to unexpected value: {llm_config.anthropic_model}")
    exit(1)
