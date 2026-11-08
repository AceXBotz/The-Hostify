"""Test package: sets safe dummy configuration before any project module imports."""
import os
import tempfile

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/hostify_test")
os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test-hash")
os.environ.setdefault("OWNER_ID", "8043083450")
os.environ.setdefault("BASE_DIR", tempfile.mkdtemp(prefix="hostify_test_"))
