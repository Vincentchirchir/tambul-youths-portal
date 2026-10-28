"""Exercise the production-backup reader against temporary local media only."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import backup_render_media as backup


class MediaBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tambul-backup-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "source"
        for subdirectory in ("minutes", "profile_photos", "protected/signatories/signatures", "empty"):
            (self.root / subdirectory).mkdir(parents=True)
        for name in ("minutes/meeting file.pdf", "profile_photos/photo.jpg", "protected/signatories/signatures/signature.png"):
            (self.root / name).write_bytes(name.encode())
        self.remote_script = backup.REMOTE_SCRIPT.replace(
            "Path('/opt/render/project/src/media')", "Path(" + repr(str(self.root)) + ")"
        )

    def copy(self, target, mode, destination):
        with destination.open("xb") as output:
            subprocess.run(
                [sys.executable, "-B", "-"],
                input=("MODE=" + repr(mode) + "\n" + self.remote_script).encode(),
                stdout=output, stderr=subprocess.PIPE, check=True,
            )

    def create_backup(self):
        destination = Path(self.directory.name) / "backup"
        argv = ["backup", "--ssh-target", "instance@example.com", "--destination", str(destination)]
        with patch.object(sys, "argv", argv), patch.object(backup, "remote_copy", side_effect=self.copy):
            self.assertEqual(backup.main(), 0)
        return destination

    def test_complete_tree_and_empty_directories_are_preserved_and_reverified(self):
        destination = self.create_backup()
        receipt = backup.verify_backup(destination)
        self.assertEqual(receipt["files"], 3)
        self.assertEqual(receipt["status"], "VERIFIED")
        self.assertEqual((destination.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((destination / "media.tar").stat().st_mode & 0o777), 0o600)

    def test_source_mutation_prevents_verified_receipt(self):
        destination = Path(self.directory.name) / "changing-backup"

        def copy_and_change(target, mode, path):
            self.copy(target, mode, path)
            if mode == "archive":
                (self.root / "minutes/new.pdf").write_bytes(b"new upload")

        argv = ["backup", "--ssh-target", "instance@example.com", "--destination", str(destination)]
        with patch.object(sys, "argv", argv), patch.object(backup, "remote_copy", side_effect=copy_and_change):
            self.assertEqual(backup.main(), 1)
        self.assertTrue((destination / "media.tar").exists())
        self.assertFalse((destination / "verified.json").exists())

    def test_checksum_mismatch_is_rejected(self):
        destination = self.create_backup()
        inventory = json.loads((destination / "inventory-before.json").read_text())
        inventory["files"]["minutes/meeting file.pdf"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            backup.verify_archive(destination / "media.tar", inventory)

    def test_symlinks_stop_inventory_without_touching_source(self):
        (self.root / "minutes/external-link").symlink_to(self.root / "profile_photos")
        result = subprocess.run(
            [sys.executable, "-B", "-"],
            input=("MODE='inventory'\n" + self.remote_script).encode(),
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Symlink requires separate review", result.stderr)
        self.assertTrue((self.root / "minutes/external-link").is_symlink())

    def test_partial_archive_is_rejected(self):
        destination = self.create_backup()
        archive = destination / "media.tar"
        archive.write_bytes(archive.read_bytes()[:100])
        with self.assertRaises(Exception):
            backup.verify_backup(destination)


if __name__ == "__main__":
    unittest.main()
