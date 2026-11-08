from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

import psutil

from config import BASE_DIR, BOT_DISK_WARN_MB, OWNER_ID
from database import list_bots, update_status
from deployer import launch_existing
from utils import send_message
import messages as M

logger = logging.getLogger("hostify.watchdog")
_PROCESS_TIME_TOLERANCE = 1.5
_CHECK_INTERVAL_SECONDS = 600
_DISK_CHECK_EVERY_N_CYCLES = 6  # roughly once an hour at the default interval


def _alive(bot: dict) -> bool:
    """Check PID liveness and creation time to avoid PID-reuse false positives."""
    pid = bot.get("pid")
    expected_created_at = bot.get("pid_created_at")

    if not isinstance(pid, int) or pid <= 0:
        return False

    try:
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False

        # Legacy records lack a creation timestamp. Treat an existing PID as
        # alive rather than launching a duplicate process; stop/delete handlers
        # still fail closed until the bot is redeployed with identity metadata.
        if not isinstance(expected_created_at, (int, float)):
            return True

        return abs(process.create_time() - float(expected_created_at)) <= _PROCESS_TIME_TOLERANCE

    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        # An inability to inspect a PID must not trigger a duplicate launch.
        logger.warning("Cannot inspect PID %s; watchdog will leave it untouched", pid)
        return True


def _directory_size_mb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for filename in files:
            try:
                total += (Path(root) / filename).stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total / (1024 * 1024)


async def _check_disk_usage(client, bots: list[dict]) -> None:
    if BOT_DISK_WARN_MB <= 0:
        return
    base = Path(BASE_DIR).expanduser().resolve()
    over_limit: list[str] = []
    for bot in bots:
        name = bot.get("name")
        if not isinstance(name, str) or not name:
            continue
        bot_dir = base / name
        if not bot_dir.is_dir() or bot_dir.is_symlink():
            continue
        size_mb = await asyncio.to_thread(_directory_size_mb, bot_dir)
        if size_mb > BOT_DISK_WARN_MB:
            over_limit.append(f"{name} ({size_mb:.0f} MB)")

    if over_limit:
        await send_message(
            client, OWNER_ID,
            M.M112_DISK_WARNING.format(
                limit=BOT_DISK_WARN_MB,
                bots=", ".join(over_limit),
            ),
        )


async def _run_cycle(client) -> None:
    bots = await list_bots()
    if not bots:
        return

    restarted: list[str] = []
    failed: list[str] = []

    for bot in bots:
        if bot.get("status") != "running":
            continue
        if _alive(bot):
            continue

        name = bot.get("name")
        if not isinstance(name, str) or not name:
            logger.error("Skipping watchdog record with invalid name")
            continue

        ok, detail = await launch_existing(name, bot.get("env_vars") or {})
        if ok:
            restarted.append(name)
        else:
            failed.append(name)
            await update_status(name, "crashed", None, None)
            logger.error("Watchdog restart failed for %s: %s", name, detail)

    if restarted or failed:
        now = datetime.now().strftime("%H:%M")
        if restarted:
            text = M.M32_WATCHDOG_RESTARTED.format(
                time=now,
                restarted=", ".join(restarted),
            )
            await send_message(client, OWNER_ID, text)
        if failed:
            text = M.M32_WATCHDOG_FAIL.format(
                time=now,
                failed=", ".join(failed),
            )
            await send_message(client, OWNER_ID, text)

    return bots


async def watchdog_loop(client) -> None:
    """Check once immediately (so a controller restart recovers crashed bots
    right away instead of after up to 10 minutes idle), then on a fixed
    interval."""
    cycle = 0
    while True:
        try:
            bots = await _run_cycle(client)
            cycle += 1
            if bots and cycle % _DISK_CHECK_EVERY_N_CYCLES == 0:
                await _check_disk_usage(client, bots)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Watchdog cycle failed")

        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
