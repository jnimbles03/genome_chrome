"""Environment for the public LLM abuse tests.

Values are obvious local placeholders. They are read by app.core.config and
app.services.storage at import time, so they must be set before those modules
load. PostgreSQL on this machine trusts 127.0.0.1 for the formgenome role, so
no database password is required.
"""
import os

os.environ["GEMINI_API_KEY"] = "placeholder-not-a-real-key"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["ENABLE_LLM_RATE_LIMITING"] = "true"
os.environ["ENABLE_LLM_FALLBACK"] = "true"
os.environ["GEMINI_RPM"] = "60"
os.environ["LLM_RATE_LIMIT_TIMEOUT"] = "0.4"
os.environ["GEMINI_MODEL"] = "gemini-2.5-flash"
os.environ["DB_HOST"] = "127.0.0.1"
os.environ["DB_PORT"] = "5432"
os.environ["DB_USER"] = "formgenome"
os.environ["DB_PASSWORD"] = ""
os.environ["DB_NAME"] = "formgenome"
os.environ["FORMGENOME_API_TOKENS"] = "client-a-token,client-b-token"
os.environ.setdefault("FLASK_DEBUG", "false")
