import fcntl
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

from deploy.backup_data import backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        with sqlite3.connect(self.data/'resume.sqlite3') as db:
            db.execute('CREATE TABLE sample (value TEXT)')
            db.execute("INSERT INTO sample VALUES ('saved account record')")
        (self.data/'auth-secret').write_text('test-secret')
        path = self.data/'users'/'test'/'workspace'
        path.mkdir(parents=True)
        (path/'项目经历库.md').write_text('saved facts')

    def test_complete_private_backup_can_restore(self):
        archive_path = backup(self.data, self.root/'backups')
        self.assertEqual(archive_path.stat().st_mode & 0o777, 0o600)
        with tarfile.open(archive_path) as archive:
            self.assertNotIn('data/runtime.lock', archive.getnames())
            self.assertEqual(archive.extractfile('data/auth-secret').read(), b'test-secret')
            self.assertEqual(archive.extractfile('data/users/test/workspace/项目经历库.md').read(), b'saved facts')
            restored = self.root/'restored.sqlite3'
            restored.write_bytes(archive.extractfile('data/resume.sqlite3').read())
        with sqlite3.connect(restored) as db:
            self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], 'saved account record')

    def test_active_runtime_refused_without_partial_backup(self):
        with (self.data/'runtime.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(RuntimeError):
                backup(self.data, self.root/'backups')
        self.assertFalse((self.root/'backups').exists())

    def test_destination_cannot_include_itself(self):
        for destination in (self.data, self.data/'backups'):
            with self.assertRaises(ValueError):
                backup(self.data, destination)
        with self.assertRaises(ValueError):
            backup(self.root, self.root/'elsewhere')

    def test_corrupt_database_is_not_reported_as_success(self):
        (self.data/'resume.sqlite3').write_bytes(b'not a sqlite database')
        with self.assertRaises(sqlite3.Error):
            backup(self.data, self.root/'backups')
        self.assertFalse((self.root/'backups').exists())
