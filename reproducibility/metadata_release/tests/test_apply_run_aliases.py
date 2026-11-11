import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.apply_run_aliases import PINNED, apply, guard


class AliasProtectionTests(unittest.TestCase):
    def test_frozen_tables_cannot_be_changed(self):
        con = sqlite3.connect(':memory:')
        for table in PINNED:
            con.execute(f'CREATE TABLE "{table}"(value TEXT)')
            con.execute(f'INSERT INTO "{table}" VALUES(?)', ('original',))
        con.commit()
        con.set_authorizer(guard)
        for table in PINNED:
            for sql in (f'UPDATE "{table}" SET value=\'changed\'',
                        f'DELETE FROM "{table}"',
                        f'INSERT INTO "{table}" VALUES(\'new\')',
                        f'DROP TABLE "{table}"',
                        f'ALTER TABLE "{table}" ADD COLUMN extra TEXT'):
                with self.subTest(table=table, sql=sql):
                    with self.assertRaises(sqlite3.DatabaseError):
                        con.execute(sql)
            self.assertEqual(con.execute(f'SELECT value FROM "{table}"').fetchall(), [('original',)])
        con.close()

    def test_alias_enrichment_keeps_inputs_and_processing_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root/'audit'
            cache = audit/'cache'
            cache.mkdir(parents=True)
            records = ''.join(f'''<EXPERIMENT_PACKAGE><SAMPLE><IDENTIFIERS>
                <EXTERNAL_ID namespace="BioSample">SAMN{i}</EXTERNAL_ID></IDENTIFIERS></SAMPLE>
                <RUN_SET><RUN accession="SRR{i+10}" is_public="true"><IDENTIFIERS>
                <SECONDARY_ID>SRR{i}</SECONDARY_ID></IDENTIFIERS></RUN></RUN_SET>
                </EXPERIMENT_PACKAGE>''' for i in (1, 2))
            payload = ('<EXPERIMENT_PACKAGE_SET>'+records+'</EXPERIMENT_PACKAGE_SET>').encode()
            (cache/'evidence.xml').write_bytes(payload)
            (cache/'evidence.request.json').write_text(json.dumps({
                'response_sha256': hashlib.sha256(payload).hexdigest(),
                'url': 'https://example.org/evidence', 'parameters': {'id': '1,2'},
                'retrieved_at': '2026-11-10T00:00:00+00:00'}))
            with (audit/'ncbi_alias_audit.tsv').open('w') as handle:
                writer = csv.writer(handle, delimiter='\t')
                writer.writerow(['sample_accession','run_accession','primary_run_accession',
                                 'audit_status','candidate_count','evidence_file'])
                for i in (1, 2):
                    writer.writerow([f'SAMN{i}',f'SRR{i}',f'SRR{i+10}',
                                     'ALIAS_PUBLIC_SAMPLE_MATCH','1','evidence.xml'])
            con = sqlite3.connect(root/'atb.metadata.r1.sqlite')
            con.executescript('''
                CREATE TABLE assembly(sample_accession TEXT PRIMARY KEY,run_accession TEXT,hq_filter TEXT);
                CREATE TABLE assembly_run_provenance(assembly_sample_accession TEXT,run_accession TEXT,observation_id INTEGER);
                CREATE TABLE ena_source(source_id TEXT,columns_json TEXT);
                CREATE TABLE ena_observation(observation_id INTEGER PRIMARY KEY,source_id TEXT,run_accession TEXT,
                    observed_date TEXT,sample_accession TEXT,fastq_md5 TEXT,fastq_bytes TEXT,base_count TEXT,read_count TEXT);
                CREATE TABLE ena_latest(run_accession TEXT,sample_accession TEXT,fastq_md5 TEXT,fastq_bytes TEXT,
                    base_count TEXT,read_count TEXT,country TEXT,atb_present_in_current_snapshot TEXT);
                CREATE TABLE assembly_ena(assembly_sample_accession TEXT,run_accession TEXT,observation_id INTEGER,
                    source_id TEXT,observed_date TEXT,selection_basis TEXT,association_match INTEGER,
                    processing_read_identity_match INTEGER,notes TEXT,sample_accession TEXT,fastq_md5 TEXT,
                    fastq_bytes TEXT,base_count TEXT,read_count TEXT,country TEXT);
                CREATE TABLE release_provenance(name TEXT,metadata_json TEXT);
            ''')
            raw_columns = ['run_accession','sample_accession','fastq_md5','fastq_bytes','base_count','read_count','country']
            con.execute('INSERT INTO ena_source VALUES(?,?)', ('ena_20260317',json.dumps(raw_columns)))
            for i in (1, 2):
                sample,old,new = f'SAMN{i}', f'SRR{i}', f'SRR{i+10}'
                con.execute('INSERT INTO assembly VALUES(?,?,?)',(sample,old,'RUN_REMOVED'))
                con.execute('INSERT INTO assembly_run_provenance VALUES(?,?,NULL)',(sample,old))
                con.execute('INSERT INTO ena_latest VALUES(?,?,?,?,?,?,?,?)',
                            (new,sample,'md5','20','100','5','UK','1'))
                con.execute('INSERT INTO ena_observation VALUES(?,?,?,?,?,?,?,?,?)',
                            (i,'ena_20260317',new,None,sample,'md5','20','100','5'))
                con.execute("INSERT INTO assembly_ena(assembly_sample_accession,run_accession,selection_basis) VALUES(?,?,'unavailable')",(sample,old))
            con.commit(); con.close()
            (root/'atb.metadata.r1.sqlite.report.json').write_text(json.dumps({
                'database': {'path':'atb.metadata.r1.sqlite'},'exports':{},
                'history_extension': {'counts':{}}}))
            apply(root,audit)
            con = sqlite3.connect(root/'atb.metadata.r1.sqlite')
            self.assertEqual(con.execute('SELECT * FROM assembly ORDER BY sample_accession').fetchall(),
                             [('SAMN1','SRR1','RUN_REMOVED'),('SAMN2','SRR2','RUN_REMOVED')])
            self.assertEqual(con.execute('SELECT observation_id FROM assembly_run_provenance').fetchall(),[(None,),(None,)])
            self.assertEqual(con.execute('SELECT run_accession,ena_run_accession,selection_basis,country FROM assembly_ena ORDER BY run_accession').fetchall(),
                             [('SRR1','SRR11','accession_alias_fallback','UK'),
                              ('SRR2','SRR12','accession_alias_fallback','UK')])
            self.assertEqual(con.execute('SELECT count(*) FROM run_alias').fetchone()[0],2)
            con.close()

    def test_descriptive_changes_and_schema_are_transactional(self):
        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE assembly_ena(run_accession TEXT)')
        con.execute("INSERT INTO assembly_ena VALUES('original')")
        con.commit()
        con.set_authorizer(guard)
        with self.assertRaises(RuntimeError):
            with con:
                con.execute('BEGIN IMMEDIATE')
                con.execute('ALTER TABLE assembly_ena ADD COLUMN ena_run_accession TEXT')
                con.execute('CREATE TABLE run_alias(recorded_run_accession TEXT)')
                con.execute("UPDATE assembly_ena SET ena_run_accession='primary'")
                raise RuntimeError('simulate failed evidence verification')
        self.assertEqual([r[1] for r in con.execute('PRAGMA table_info(assembly_ena)')], ['run_accession'])
        self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='run_alias'").fetchone())
        con.close()
