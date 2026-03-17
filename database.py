"""
Single MongoDB connection for the app. Load config from environment.
"""
import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# Prefer MONGODB_URI; otherwise build from MONGO_* or MONGODB_* env vars
_mongo_uri = os.getenv("MONGODB_URI")
if _mongo_uri:
    client = MongoClient(_mongo_uri)
    db = client.get_database()
else:
    host = os.getenv("MONGO_HOST") or os.getenv("MONGODB_HOST", "localhost")
    port = int(os.getenv("MONGO_PORT") or os.getenv("MONGODB_PORT", "27017"))
    username = (os.getenv("MONGO_USERNAME") or os.getenv("MONGODB_USERNAME", "")).strip() or None
    password = (os.getenv("MONGO_PASSWORD") or os.getenv("MONGODB_PASSWORD", "")).strip() or None
    auth_source = (os.getenv("MONGO_AUTH_SOURCE") or os.getenv("MONGODB_AUTH_SOURCE", "Infinitytest")).strip() or "Infinitytest"
    db_name = (os.getenv("MONGO_DATABASE") or os.getenv("MONGODB_DATABASE", "Infinitytest")).strip() or "Infinitytest"
    # Use URI so password special characters (e.g. !) are handled correctly
    if username and password:
        encoded_user = quote_plus(username)
        encoded_pass = quote_plus(password)
        _mongo_uri = f"mongodb://{encoded_user}:{encoded_pass}@{host}:{port}/{db_name}?authSource={quote_plus(auth_source)}"
        client = MongoClient(_mongo_uri)
    else:
        client = MongoClient(host=host, port=port, authSource=auth_source)
    db = client[db_name]

customers = db["customers"]
tickets = db["tickets"]
agents = db["agents"]
admins = db["admins"]
users = db["users"]
audit_logs = db["audit_logs"]
