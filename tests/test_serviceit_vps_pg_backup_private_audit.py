"""Offline, network-free regression checks for actual native private backup audit."""
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest

from ops.serviceit_vps_pg_backup_private_audit import probe, CRON_MARKER


class PrivateBackupAuditTests(unittest.TestCase):
    def test_legacy_archive_does_not_count_as_native_private_backup(self):
        with tempfile.TemporaryDirectory() as d:
            data = probe(home=Path(d), now=100000, run=self.fake_run)
            self.assertEqual(data["PRIVATE_DIR_EXISTS"], "NO")
            self.assertEqual(data["PRIVATE_ARCHIVE_COUNT"], "UNVERIFIED")
            self.assertEqual(data["PRIVATE_NATIVE_CRON_INSTALLED"], "NO")

    @staticmethod
    def fake_run(args, **kwargs):
        if args[:2] == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0, stdout="")
        if args[:3] == ["systemctl", "is-active", "cron"]:
            return SimpleNamespace(returncode=0, stdout="active\n")
        return SimpleNamespace(returncode=0, stdout="")

    def test_private_archives_toc_and_exact_cron_without_exposing_file_names(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            root = home / "serviceit" / "backups"
            root.mkdir(parents=True, mode=0o700)
            root.chmod(0o700)
            first = root / "svc-it-leadgen-20261119T021700Z-1234abcd.dump"
            second = root / "svc-it-leadgen-20261120T021700Z-deadbeef.dump"
            for f in (first, second):
                f.write_bytes(b"PGDMPsynthetic")
                f.chmod(0o600)
            (root / "other-company.dump").write_bytes(b"PGDMPsynthetic")
            os.utime(first, (90000, 90000))
            os.utime(second, (99000, 99000))
            program = home / "serviceit" / "ops" / "serviceit_vps_pg_backup.py"
            log = home / "serviceit" / "ops" / "backup-scheduler.log"
            expected_cron = ("17 2 * * * /usr/bin/python3 " + str(program)
                             + " --backup-only >> " + str(log)
                             + " 2>&1 " + CRON_MARKER)

            def run(args, **kwargs):
                if args[:2] == ["crontab", "-l"]:
                    return SimpleNamespace(returncode=0, stdout=expected_cron + "\n")
                if args[:3] == ["systemctl", "is-active", "cron"]:
                    return SimpleNamespace(returncode=0, stdout="active\n")
                return SimpleNamespace(returncode=0, stdout="")
            result = probe(home=home, now=100000, run=run)
            self.assertEqual(result["PRIVATE_ARCHIVE_COUNT"], "2")
            self.assertEqual(result["PRIVATE_NEWEST_AGE_HOURS"], "0")
            self.assertEqual(result["PRIVATE_NEWEST_PGDUMP_MAGIC"], "YES")
            self.assertEqual(result["PRIVATE_NEWEST_TOC_DECODED"], "YES")
            self.assertEqual(result["PRIVATE_NEWEST_FULL_DECODED"], "YES")
            self.assertEqual(result["PRIVATE_NATIVE_CRON_INSTALLED"], "YES")
            self.assertEqual(result["PRIVATE_NATIVE_CRON_ACTIVE"], "YES")
            self.assertEqual(result["PRIVATE_ISOLATED_RESTORE_CURRENT"], "NOT_TESTED")
            self.assertNotIn(str(root), str(result))
            self.assertNotIn(first.name, str(result))

    def test_world_readable_directory_must_not_be_inspected(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            root = home / "serviceit" / "backups"
            root.mkdir(parents=True, mode=0o755)
            root.chmod(0o755)
            result = probe(home=home, run=self.fake_run)
            self.assertEqual(result["PRIVATE_DIR_OWNER_MODE_OK"], "NO")
            self.assertEqual(result["PRIVATE_ARCHIVE_COUNT"], "UNVERIFIED")

    def test_wrong_cron_marker_remains_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            def run(args, **kwargs):
                if args[:2] == ["crontab", "-l"]:
                    return SimpleNamespace(returncode=0, stdout="17 2 * * * echo synthetic " + CRON_MARKER)
                return SimpleNamespace(returncode=0, stdout="active\n")
            result = probe(home=Path(d), run=run)
            self.assertEqual(result["PRIVATE_NATIVE_CRON_INSTALLED"], "NO")


if __name__ == "__main__":
    unittest.main()
