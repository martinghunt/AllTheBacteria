import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.ena_history import association_match, export, fingerprint, iso_date
from atb_release_metadata.release_query_exports import build as export_release_join
from atb_release_metadata.io import sha256_file


class HistoryTest(unittest.TestCase):
    def test_associations_are_token_based(self):
        self.assertTrue(association_match('SAMN1', 'SAMN1;SAMN2'))
        self.assertTrue(association_match('SAMN2', 'SAMN1,SAMN2'))
        self.assertFalse(association_match('SAMN1', 'SAMN10'))
        self.assertFalse(association_match('SAMN1', None))

    def test_retrieval_dates_do_not_invent_missing_dates(self):
        self.assertEqual(iso_date('20240913'), '2024-09-13')
        self.assertEqual(iso_date('2024-09-13'), '2024-09-13')
        self.assertIsNone(iso_date(None))

    def test_export_preserves_pairs_and_marks_unavailable_metadata(self):
        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE assembly_ena(assembly_sample_accession TEXT,run_accession TEXT,country TEXT)')
        con.executemany('INSERT INTO assembly_ena VALUES(?,?,?)', [('S1','R1','UK'),('S1','R2',None)])
        before = fingerprint(con, 'assembly_ena')
        with tempfile.TemporaryDirectory() as directory:
            result = export(con, 'assembly_ena', Path(directory))
            self.assertEqual(result['rows'], 2)
            text = gzip.decompress((Path(directory)/result['path']).read_bytes()).decode()
            self.assertIn('S1\tR1\tUK\nS1\tR2\tNA\n', text)
        self.assertEqual(before, fingerprint(con, 'assembly_ena'))
        con.close()

    def test_join_export_keeps_multiple_runs_and_unknown_run_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); database=root/'atb.metadata.r1.sqlite'
            con=sqlite3.connect(database)
            con.executescript('''
              CREATE TABLE assembly(sample_accession TEXT,dataset TEXT,sylph_species TEXT,hq_filter TEXT,aws_url TEXT);
              INSERT INTO assembly VALUES('S1','661k','Species','PASS','u1'),('S2','r0.2','Species','NO_RUNS','u2');
              CREATE TABLE release_membership(release_id TEXT,sample_accession TEXT);
              INSERT INTO release_membership VALUES('1','S1'),('1','S2');
              CREATE TABLE assembly_ena_metadata(assembly_sample_accession TEXT,run_accession TEXT,selection_basis TEXT,notes TEXT,country TEXT);
              INSERT INTO assembly_ena_metadata VALUES('S1','R1','historical_inference','n','UK'),('S1','R2','historical_inference','n','UK');
            '''); con.close()
            (root/'atb.metadata.r1.sqlite.report.json').write_text(json.dumps({'exports':{}}))
            before=sha256_file(database)
            export_release_join(root)
            self.assertEqual(before,sha256_file(database))
            result=json.loads((root/'atb.metadata.r1.sqlite.report.json').read_text())['exports']['release_1_assembly_ena_metadata']
            self.assertEqual(result['rows'],3)
            self.assertEqual(result['distinct_assembly_samples'],2)
            text=gzip.decompress((root/result['path']).read_bytes()).decode()
            self.assertIn('S2\tNA\tunavailable\t',text)
