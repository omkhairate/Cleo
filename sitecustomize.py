import os


# FastAPI imports Pydantic during startup, and on this machine Pydantic's
# plugin auto-discovery can hang while scanning installed distributions.
# Disable plugin discovery unless the user explicitly overrides it.
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
