import asyncio
import unittest
from unittest import mock

import watchdog


class WatchdogStartupTests(unittest.TestCase):
    def test_first_cycle_runs_immediately_without_waiting(self):
        """A controller restart must recover dead bots right away, not after the interval."""
        calls = []

        async def fake_cycle(client):
            calls.append("cycle")
            return []

        async def run():
            sleep_started = asyncio.Event()

            async def fake_sleep(seconds):
                sleep_started.set()
                raise asyncio.CancelledError

            with mock.patch.object(watchdog, "_run_cycle", fake_cycle), \
                 mock.patch.object(watchdog.asyncio, "sleep", fake_sleep):
                with self.assertRaises(asyncio.CancelledError):
                    await watchdog.watchdog_loop(object())
            return list(calls)

        self.assertEqual(asyncio.run(run()), ["cycle"])

    def test_dead_bot_is_restarted_and_owner_notified(self):
        sent = []

        async def fake_send(client, chat_id, text, *a, **k):
            sent.append(text)

        async def run():
            bots = [{"name": "alpha", "status": "running", "pid": 999999, "pid_created_at": 1.0, "env_vars": {}}]
            with mock.patch.object(watchdog, "list_bots", mock.AsyncMock(return_value=bots)), \
                 mock.patch.object(watchdog, "launch_existing", mock.AsyncMock(return_value=(True, "4242"))), \
                 mock.patch.object(watchdog, "send_message", fake_send):
                await watchdog._run_cycle(object())

        asyncio.run(run())
        self.assertEqual(len(sent), 1)
        self.assertIn("alpha", sent[0])

    def test_restart_and_failure_are_both_reported(self):
        sent = []

        async def fake_send(client, chat_id, text, *a, **k):
            sent.append(text)

        async def fake_launch(name, env):
            return (True, "1") if name == "good" else (False, "boom")

        async def run():
            bots = [
                {"name": "good", "status": "running", "pid": 999998, "pid_created_at": 1.0},
                {"name": "bad", "status": "running", "pid": 999997, "pid_created_at": 1.0},
            ]
            with mock.patch.object(watchdog, "list_bots", mock.AsyncMock(return_value=bots)), \
                 mock.patch.object(watchdog, "launch_existing", fake_launch), \
                 mock.patch.object(watchdog, "update_status", mock.AsyncMock()), \
                 mock.patch.object(watchdog, "send_message", fake_send):
                await watchdog._run_cycle(object())

        asyncio.run(run())
        self.assertEqual(len(sent), 2)
        self.assertTrue(any("good" in t for t in sent))
        self.assertTrue(any("bad" in t for t in sent))


class DiskCheckTests(unittest.TestCase):
    def test_flags_only_oversize_directories(self):
        import tempfile
        from pathlib import Path
        sent = []

        async def fake_send(client, chat_id, text, *a, **k):
            sent.append(text)

        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                big = Path(tmp) / "big"; big.mkdir()
                (big / "blob").write_bytes(b"0" * (2 * 1024 * 1024))
                small = Path(tmp) / "small"; small.mkdir()
                (small / "f").write_bytes(b"0")
                with mock.patch.object(watchdog, "BASE_DIR", tmp), \
                     mock.patch.object(watchdog, "BOT_DISK_WARN_MB", 1), \
                     mock.patch.object(watchdog, "send_message", fake_send):
                    await watchdog._check_disk_usage(object(), [{"name": "big"}, {"name": "small"}])

        asyncio.run(run())
        self.assertEqual(len(sent), 1)
        self.assertIn("big", sent[0])
        self.assertNotIn("small", sent[0])


if __name__ == "__main__":
    unittest.main()
