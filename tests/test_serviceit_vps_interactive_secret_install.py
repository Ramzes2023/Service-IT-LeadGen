"""Unit tests for VPS local-only secret loader. No real secrets or network."""
import importlib.util
from pathlib import Path
import os
import stat
import tempfile
import io
from contextlib import redirect_stdout
import types
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "serviceit-vps-interactive-secret-install.py"
spec = importlib.util.spec_from_file_location("vps_secret_installer", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LocalSecretInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.dirfd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.dirfd)

    def _tty(self):
        stdin = mock.patch.object(
            module.sys, "stdin", types.SimpleNamespace(isatty=lambda: True)
        )
        stderr = mock.patch.object(
            module.sys, "stderr", types.SimpleNamespace(isatty=lambda: True)
        )
        self.addCleanup(stdin.stop)
        self.addCleanup(stderr.stop)
        stdin.start()
        stderr.start()

    def test_install_hidden_one_line_never_overwrites(self):
        self._tty()
        value = "ONLY_TEST_VALUE_NOT_A_REAL_SECRET"
        with mock.patch.object(module.getpass, "getpass", return_value=value), \
             mock.patch.object(module.os, "fchown"):
            module.put(self.dirfd, "routerai_key")
            self.assertEqual((self.directory / "routerai_key").read_text(), value)
            self.assertEqual(
                stat.S_IMODE((self.directory / "routerai_key").stat().st_mode), 0o400
            )
            module.put(self.dirfd, "routerai_key")
        self.assertEqual((self.directory / "routerai_key").read_text(), value)

    def test_reject_noninteractive(self):
        with self.assertRaisesRegex(ValueError, "INTERACTIVE_TTY_REQUIRED"):
            module.put(self.dirfd, "brave_api_key")

    def test_reject_newline(self):
        self._tty()
        with mock.patch.object(module.getpass, "getpass", return_value="a\nb"):
            with self.assertRaisesRegex(ValueError, "INVALID"):
                module.put(self.dirfd, "gigachat_credentials")
        self.assertFalse((self.directory / "gigachat_credentials").exists())

    def test_poll_only_inventory_is_exactly_seven_safe_names(self):
        expected = (
            "pg_password", "bitrix_webhook", "event_bridge_key",
            "gh_ledger_key", "github_ledger_pat", "brave_api_key",
            "routerai_key",
        )
        self.assertEqual(module.POLL_ONLY_REQUIRED, expected)
        output = io.StringIO()
        with redirect_stdout(output):
            module.inventory(self.dirfd, module.POLL_ONLY_REQUIRED)
        self.assertEqual(output.getvalue().splitlines(),
                         [name + "=MISSING" for name in expected])
        self.assertNotIn("gigachat_credentials", output.getvalue())
        self.assertNotIn("bitrix_app_token", output.getvalue())

    def test_disallow_existing_password_names(self):
        with self.assertRaisesRegex(ValueError, "NOT_ALLOWED"):
            module.put(self.dirfd, "pg_password")
        self.assertFalse((self.directory / "pg_password").exists())

    def test_disallow_symlink_overwrite(self):
        target = self.directory / "target"
        target.write_text("keep")
        (self.directory / "routerai_key").symlink_to(target)
        module.put(self.dirfd, "routerai_key")
        self.assertEqual(target.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
