"""Environment for the paid-analysis access tests.

Must run before the application is imported. Config and storage read these
values at import time.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Placeholder values only. These are not credentials for any real service.
os.environ["GEMINI_API_KEY"] = "placeholder-gemini-key"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GEMINI_RPM"] = "10000"
os.environ["ENABLE_LLM_RATE_LIMITING"] = "true"
os.environ["LLM_RATE_LIMIT_TIMEOUT"] = "1"
os.environ["DB_HOST"] = "127.0.0.1"
os.environ["DB_PORT"] = "5432"
os.environ["DB_USER"] = "formgenome"
os.environ["DB_PASSWORD"] = ""
os.environ["DB_NAME"] = "formgenome"
os.environ["FORMGENOME_API_TOKENS"] = (
    "placeholder-token-alice:alice,placeholder-token-bob:bob"
)
os.environ["FORMGENOME_MAX_BATCH_URLS"] = "10"
os.environ["FORMGENOME_MAX_DISCOVERY_FORMS"] = "20"
os.environ["FORMGENOME_DAILY_UPLOADS"] = "3"
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
