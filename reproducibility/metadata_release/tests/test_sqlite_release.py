import sqlite3
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.sqlite_release import load, insert


class SqliteReleaseTest(unittest.TestCase):
    def test_preserves_literal_quotes_and_numeric_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.tsv'
            path.write_text('sample\tbase_count\tflag\tcomments\nS1\t1234567890123\t1\tvalue "quoted"\n')
            con = sqlite3.connect(':memory:')
            load(con, 'assembly', path, 'sample', {'flag'})
            self.assertEqual(con.execute('SELECT * FROM assembly').fetchone(),
                             ('S1', '1234567890123', 1, 'value "quoted"'))
            with self.assertRaises(sqlite3.IntegrityError):
                insert(con, 'assembly', path, ['sample', 'base_count', 'flag', 'comments'])
            con.close()

    def test_header_mismatch_fails_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.tsv'
            path.write_text('sample\twrong\nS1\tx\n')
            con = sqlite3.connect(':memory:')
            con.execute('CREATE TABLE target(sample TEXT, expected TEXT)')
            with self.assertRaisesRegex(ValueError, 'column mismatch'):
                insert(con, 'target', path, ['sample', 'expected'])
            con.close()
