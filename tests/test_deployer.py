import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import deployer


class RepoUrlValidationTests(unittest.TestCase):
    def test_accepts_plain_github_url(self):
        self.assertEqual(
            deployer._validate_repo_url("https://github.com/octocat/Hello-World"),
            "https://github.com/octocat/Hello-World.git",
        )

    def test_rejects_embedded_credentials(self):
        for url in (
            "https://token@github.com/o/r",
            "https://user:pass@github.com/o/r",
        ):
            with self.assertRaises(ValueError):
                deployer._validate_repo_url(url)

    def test_rejects_other_hosts_schemes_and_extras(self):
        for url in (
            "http://github.com/o/r",
            "https://gitlab.com/o/r",
            "https://github.com.evil.com/o/r",
            "https://github.com/o/r?x=1",
            "https://github.com/o/r#frag",
            "https://github.com:8443/o/r",
            "https://github.com/o",
            "https://github.com/o/r/extra",
            "git@github.com:o/r.git",
            "file:///etc/passwd",
        ):
            with self.assertRaises(ValueError, msg=url):
                deployer._validate_repo_url(url)


class NameAndEnvValidationTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(deployer._validate_name("customer_1"), "customer_1")
        for bad in ("", "../etc", "a/b", "a b", "x" * 33, "name;rm", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                deployer._validate_name(bad)

    def test_env_accepts_normal_values(self):
        env = deployer._validate_env_vars({"BOT_TOKEN": "1:abc", "NOTIFY_CHANNEL": "@x_chan"})
        self.assertEqual(env["BOT_TOKEN"], "1:abc")

    def test_env_rejects_runtime_hijacking_keys(self):
        for key in ("PATH", "LD_PRELOAD", "PYTHONPATH", "PYTHONSTARTUP", "PIP_INDEX_URL", "GIT_SSH_COMMAND", "HOME"):
            with self.assertRaises(ValueError, msg=key):
                deployer._validate_env_vars({key: "x"})

    def test_env_rejects_bad_names_nulls_and_oversize(self):
        with self.assertRaises(ValueError):
            deployer._validate_env_vars({"lower": "x"})
        with self.assertRaises(ValueError):
            deployer._validate_env_vars({"A": "x\x00y"})
        with self.assertRaises(ValueError):
            deployer._validate_env_vars({"A": "x" * 5000})
        with self.assertRaises(ValueError):
            deployer._validate_env_vars({f"K{i}": "v" for i in range(41)})


class PathSafetyTests(unittest.TestCase):
    def test_bot_directory_stays_inside_base(self):
        path = deployer._bot_directory("alice")
        self.assertEqual(path.parent, Path(deployer.BASE_DIR).resolve())

    def test_bot_directory_rejects_traversal(self):
        with self.assertRaises(ValueError):
            deployer._bot_directory("../escape")


class ResourceLimitTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX only")
    def test_limits_are_actually_applied_to_a_real_child(self):
        async def run():
            with mock.patch.object(deployer, "BOT_MEMORY_LIMIT_MB", 300), \
                 mock.patch.object(deployer, "BOT_MAX_OPEN_FILES", 128), \
                 mock.patch.object(deployer, "BOT_NICE_LEVEL", 5):
                proc = await asyncio.create_subprocess_exec(
                    "python3", "-c",
                    "import resource,os;"
                    "print(resource.getrlimit(resource.RLIMIT_AS)[0]);"
                    "print(resource.getrlimit(resource.RLIMIT_NOFILE)[0]);"
                    "print(os.nice(0))",
                    stdout=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    preexec_fn=deployer._preexec_limits(),
                )
                out, _ = await proc.communicate()
                return proc.returncode, out.decode().split()

        code, lines = asyncio.run(run())
        self.assertEqual(code, 0)
        self.assertEqual(int(lines[0]), 300 * 1024 * 1024)
        self.assertEqual(int(lines[1]), 128)
        self.assertGreaterEqual(int(lines[2]), 5)


class LogRotationTests(unittest.TestCase):
    def test_small_log_untouched_and_big_log_trimmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "bot.log"
            log.write_bytes(b"small\n")
            with mock.patch.object(deployer, "LOG_ROTATE_BYTES", 1000), \
                 mock.patch.object(deployer, "LOG_ROTATE_KEEP_BYTES", 100):
                deployer._rotate_log_if_needed(log)
                self.assertEqual(log.read_bytes(), b"small\n")

                log.write_bytes(b"A" * 5000 + b"TAIL-MARKER")
                deployer._rotate_log_if_needed(log)
                data = log.read_bytes()
                self.assertLess(len(data), 400)
                self.assertTrue(data.endswith(b"TAIL-MARKER"))
                self.assertIn(b"log rotated", data)
                self.assertFalse(log.with_suffix(".tmp").exists())

    def test_symlink_log_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.txt"
            target.write_bytes(b"B" * 5000)
            link = Path(tmp) / "bot.log"
            link.symlink_to(target)
            with mock.patch.object(deployer, "LOG_ROTATE_BYTES", 10):
                deployer._rotate_log_if_needed(link)
            self.assertEqual(target.read_bytes(), b"B" * 5000)


class DeployRollbackTests(unittest.TestCase):
    def test_failed_clone_leaves_no_directory_and_allows_retry(self):
        async def run():
            fake_run = mock.AsyncMock(return_value=(128, "fatal: boom"))
            with mock.patch.object(deployer, "ALLOW_UNSANDBOXED_DEPLOYMENTS", True), \
                 mock.patch.object(deployer, "get_bot", mock.AsyncMock(return_value=None)), \
                 mock.patch.object(deployer, "_run_logged", fake_run):
                async def fake_clone(args, **kw):
                    # emulate git leaving a partial directory behind
                    Path(args[-1]).mkdir(parents=True, exist_ok=True)
                    (Path(args[-1]) / "junk").write_text("x")
                    return 128, "fatal"
                fake_run.side_effect = fake_clone
                first = await deployer.deploy_bot("rb_one", "https://github.com/o/r", {})
                exists_after = deployer._bot_directory("rb_one").exists()
                second = await deployer.deploy_bot("rb_one", "https://github.com/o/r", {})
                return first, exists_after, second

        first, exists_after, second = asyncio.run(run())
        self.assertFalse(first[0])
        self.assertFalse(exists_after)
        self.assertNotIn("already exists", second[1])

    def test_disabled_flag_blocks_deploy(self):
        async def run():
            with mock.patch.object(deployer, "ALLOW_UNSANDBOXED_DEPLOYMENTS", False):
                return await deployer.deploy_bot("x1", "https://github.com/o/r", {})
        ok, detail = asyncio.run(run())
        self.assertFalse(ok)
        self.assertIn("disabled", detail)


if __name__ == "__main__":
    unittest.main()
