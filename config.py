from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "8043083450"))
MONGO_URI = os.getenv("MONGO_URI", "").strip()
BASE_DIR = str(Path(os.path.expanduser(os.getenv("BASE_DIR", "~/hostify_bots"))).resolve())
ALLOW_UNSANDBOXED_DEPLOYMENTS = (
    os.getenv("ALLOW_UNSANDBOXED_DEPLOYMENTS", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

# Best-effort per-process guardrails for the current venv/subprocess execution
# model. These are NOT a replacement for container isolation (RLIMIT_AS caps
# virtual memory, not a hard cgroup RSS limit; there is no real CPU quota
# without cgroups) -- they exist to stop one runaway or malicious bot from
# taking down the controller or every other customer's bot on the same host.
BOT_MEMORY_LIMIT_MB = int(os.getenv("BOT_MEMORY_LIMIT_MB", "512"))
BOT_NICE_LEVEL = int(os.getenv("BOT_NICE_LEVEL", "10"))
# NOTE: RLIMIT_NPROC counts processes AND threads for the whole Linux user, not
# per bot. Keep this high when many bots share one account; it is a fork-bomb
# backstop for the host, not a per-customer quota.
BOT_MAX_PROCESSES = int(os.getenv("BOT_MAX_PROCESSES", "4096"))
BOT_MAX_OPEN_FILES = int(os.getenv("BOT_MAX_OPEN_FILES", "256"))

# Log rotation: once bot.log exceeds this size it is truncated down to the
# tail end before the next launch, so one chatty bot cannot fill the disk.
LOG_ROTATE_BYTES = int(os.getenv("LOG_ROTATE_BYTES", str(5 * 1024 * 1024)))  # 5 MB
LOG_ROTATE_KEEP_BYTES = int(os.getenv("LOG_ROTATE_KEEP_BYTES", str(512 * 1024)))  # 512 KB

# Storage visibility: watchdog flags (does not delete) any bot directory over
# this size so you find out before the disk does. 0 disables the check.
BOT_DISK_WARN_MB = int(os.getenv("BOT_DISK_WARN_MB", "1024"))

Path(BASE_DIR).mkdir(parents=True, exist_ok=True)

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is required; refusing to start without a database URI.")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required.")
if not API_ID or not API_HASH:
    raise RuntimeError("API_ID and API_HASH are required.")
if OWNER_ID <= 0:
    raise RuntimeError("OWNER_ID must be a positive Telegram user ID.")
