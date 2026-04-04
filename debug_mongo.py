from app.config import settings
from pymongo import MongoClient
from pymongo.errors import ConfigurationError

print(f"DEBUG: Using settings.MONGO_URI: '{settings.MONGO_URI}'")

try:
    client = MongoClient(
        settings.MONGO_URI,
        serverSelectionTimeoutMS=5000,
        appName="SanjeevaniRxAI"
    )
    print("DEBUG: MongoClient initialized successfully.")
    # Try a simple ping
    client.admin.command('ping')
    print("DEBUG: Connection successful! (Ping passed)")
except ConfigurationError as e:
    print(f"DEBUG: ConfigurationError: {e}")
except Exception as e:
    print(f"DEBUG: Exception: {type(e).__name__}: {e}")
