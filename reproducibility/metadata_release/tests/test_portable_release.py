import gzip
import hashlib
import json
import lzma
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.portable_provenance import migrate
from atb_release_metadata.release_checksums import finalize


class PortableReleaseTest(unittest.TestCase):
    def test_migration_and_relocated_checksum_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'atb.metadata.r1.sqlite'
            con = sqlite3.connect(database)
            con.execute('CREATE TABLE release_provenance(name TEXT, metadata_json TEXT)')
            con.execute('INSERT INTO release_provenance VALUES (?,?)', ('r1', json.dumps({'inputs': {'old': {'path': '/private/build/old.sqlite', 'sha256': 'abc'}}})))
            con.commit(); con.close()
            report = {'database': {'path': str(database), 'sha256': ''},
                      'inputs': {'old': {'path': '/private/build/old.sqlite', 'sha256': 'abc'}},
                      'exports': {'release_provenance': {'path': str(root/'atb.metadata.r1.release_provenance.tsv.gz'), 'rows': 1}}}
            report_path = root/'atb.metadata.r1.sqlite.report.json'
            report_path.write_text(json.dumps(report))
            migrate(root)
            clean = json.loads(report_path.read_text())
            self.assertEqual(clean['inputs']['old']['path'], 'old.sqlite')
            self.assertNotIn('/private/build', gzip.decompress((root/'atb.metadata.r1.release_provenance.tsv.gz').read_bytes()).decode())
            with lzma.open(str(database)+'.xz', 'wb') as handle:
                handle.write(database.read_bytes())
            evidence = root/'atb.metadata.r1.run_alias.evidence.01.xml'
            evidence.write_bytes(b'<EXPERIMENT_PACKAGE_SET/>')
            clean['supporting_files'] = {evidence.name: {
                'path': evidence.name, 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}}
            separate = root/'original_metadata.tsv.gz'
            separate.write_bytes(gzip.compress(b'sample\nS1\n'))
            clean['separate_files'] = {'original_metadata': {
                'path': separate.name, 'sha256':hashlib.sha256(separate.read_bytes()).hexdigest()}}
            clean['distribution'] = {'include_uncompressed_database':False}
            report_path.write_text(json.dumps(clean))
            finalize(root)
            self.assertNotIn(directory, report_path.read_text())
            for line in (root/'MD5SUMS').read_text().splitlines():
                digest, filename = line.split()
                self.assertEqual(digest, hashlib.md5((root/filename).read_bytes()).hexdigest())
            self.assertEqual(len((root/'SHA256SUMS').read_text().splitlines()), len((root/'MD5SUMS').read_text().splitlines()))
            self.assertIn(evidence.name, (root/'SHA256SUMS').read_text())
            self.assertIn(evidence.name, (root/'MD5SUMS').read_text())
            names = {line.split()[1] for line in (root/'SHA256SUMS').read_text().splitlines()}
            self.assertIn(separate.name,names)
            self.assertIn(database.name+'.xz',names)
            self.assertNotIn(database.name,names)
            evidence.write_bytes(b'altered')
            with self.assertRaisesRegex(ValueError, 'supporting evidence changed'):
                finalize(root)
