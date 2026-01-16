import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

class Config:
    """Application Configuration"""

    # Server Settings
    PORT = int(os.getenv("PORT", 8080))
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
    ENV = os.getenv("ENV", "production")

    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/formgenome")

    # LLM Settings
    ENABLE_LLM_FALLBACK = os.getenv("ENABLE_LLM_FALLBACK", "true").lower() == "true"
    LLM_CONFIDENCE_THRESHOLD = float(os.getenv("LLM_CONFIDENCE_THRESHOLD", "0.7"))
    LLM_COMPLEXITY_THRESHOLD = int(os.getenv("LLM_COMPLEXITY_THRESHOLD", "75"))
    
    # Rate Limiting
    ENABLE_LLM_RATE_LIMITING = os.getenv("ENABLE_LLM_RATE_LIMITING", "true").lower() == "true"
    LLM_RATE_LIMIT_TIMEOUT = float(os.getenv("LLM_RATE_LIMIT_TIMEOUT", "60.0"))
    GEMINI_RPM = int(os.getenv("GEMINI_RPM", "60"))
    ANTHROPIC_RPM = int(os.getenv("ANTHROPIC_RPM", "50"))

    # API Keys
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

    # Models
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash") # Updated to correct 2.5 flash name
    
    # Logic to select best available Anthropic model
    # Priority: Env Var (Sane) -> Opus (Fallback)
    _anthropic_env = os.getenv("ANTHROPIC_MODEL")
    
    # SYSTEM SAFETY: Block known invalid/404 models that might be stuck in env
    INVALID_MODELS = [
        "claude-3-5-sonnet-20240620", # Does not exist / 404
        "claude-3-5-sonnet-latest"    # 404 for some keys
    ]

    if _anthropic_env and _anthropic_env not in INVALID_MODELS:
        ANTHROPIC_MODEL = _anthropic_env
    else:
        # Default to the most stable high-performance model
        ANTHROPIC_MODEL = "claude-3-opus-20240229"
        if _anthropic_env in INVALID_MODELS:
            print(f"WARNING: Forced override of invalid model '{_anthropic_env}' to '{ANTHROPIC_MODEL}'")

    @classmethod
    def print_config(cls):
        print("="*60)
        print("CONFIGURATION LOADED")
        print(f"ENV: {cls.ENV}")
        print(f"LLM Fallback Enabled: {cls.ENABLE_LLM_FALLBACK}")
        print(f"Gemini Model: {cls.GEMINI_MODEL}")
        print(f"Anthropic Model: {cls.ANTHROPIC_MODEL}")
        print("="*60)
