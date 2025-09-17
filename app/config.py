import os

# ENV / Secrets
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET_RAW = os.getenv("APP_SECRET", "")  # bytes lo hacemos en wa_api
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")

# Meta / Graph base
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Sanity checks mínimos (opcionales):
REQUIRED_ENV = ["WHATSAPP_TOKEN", "PHONE_NUMBER_ID", "GRAPH_API_VERSION"]
