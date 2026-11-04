"""Settings for the whole test session, applied before any test module is imported."""

import os

# rag.api.app builds its app when imported, from the environment and .env. A FRONTEND_DIST set there (for running
# the built web app) would make that fail wherever it doesn't resolve, and every API test with it. Tests that serve
# the web app pass their own FRONTEND_DIST; an empty value here also keeps .env's out (load_dotenv never overrides).
os.environ["FRONTEND_DIST"] = ""
