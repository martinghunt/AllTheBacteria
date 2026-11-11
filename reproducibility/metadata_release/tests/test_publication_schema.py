import json
import sqlite3
import unittest

from atb_release_metadata.publication_schema import transform


class PublicationSchemaTest(unittest.TestCase):
    def fixture(self):
        con = sqlite3.connect(':memory:')
        con.executescript('''
          CREATE TABLE assembly(sample_accession TEXT PRIMARY KEY,hq_filter TEXT);
          INSERT INTO assembly VALUES('S1','RUN_REMOVED');
          CREATE TABLE release_membership(sample_accession TEXT);
          INSERT INTO release_membership VALUES('S1');
          CREATE TABLE assembly_ena(assembly_sample_accession TEXT,run_accession TEXT,ena_run_accession TEXT,country TEXT);
          INSERT INTO assembly_ena VALUES('S1','Rold','Rnew','UK'),('S1','R2','R2',NULL);
          CREATE TABLE assembly_run_provenance(sample_accession TEXT,observation_id INTEGER);
          INSERT INTO assembly_run_provenance VALUES('S1',NULL);
          CREATE TABLE ena_latest(run_accession TEXT PRIMARY KEY,sample_accession TEXT,fastq_md5 TEXT,
            fastq_bytes TEXT,base_count TEXT,fastq_ftp TEXT,read_count TEXT,country TEXT,
            atb_ever_fastq_md5_changed TEXT,atb_present_in_current_snapshot TEXT);
          INSERT INTO ena_latest VALUES('Rnew','S1','a;b','10;20','123','u;v','7','UK','1','0');
          INSERT INTO ena_latest VALUES('R2','S1','NA','NA','0','NA','0','NA','0','1');
          CREATE TABLE ena_661k_sample(sample_id TEXT);
          INSERT INTO ena_661k_sample VALUES('S1');
          CREATE TABLE release_provenance(name TEXT,metadata_json TEXT);
        ''')
        return con

    def test_preserves_selected_metadata_and_exact_latest_values(self):
        con = self.fixture()
        before = con.execute('SELECT * FROM assembly_ena').fetchall()
        columns = transform(con,{'ena_latest':{'path':'latest.tsv.gz','sha256':'example'}})
        self.assertEqual(con.execute('SELECT * FROM assembly_ena_metadata').fetchall(),before)
        self.assertEqual(con.execute('SELECT * FROM assembly').fetchall(),[('S1','RUN_REMOVED')])
        self.assertEqual(con.execute('SELECT * FROM release_membership').fetchall(),[('S1',)])
        self.assertEqual(con.execute('SELECT * FROM assembly_run_provenance').fetchall(),[('S1',None)])
        self.assertEqual(con.execute("SELECT name FROM sqlite_master WHERE name IN ('ena_latest','ena_661k_sample','assembly_ena')").fetchall(),[])
        self.assertIn('atb_ever_fastq_md5_changed',columns)
        self.assertEqual(con.execute("SELECT latest_fastq_md5,latest_fastq_bytes,latest_base_count,latest_fastq_ftp,latest_read_count FROM ena_run_history WHERE run_accession='Rnew'").fetchone(),
                         ('a;b','10;20','123','u;v','7'))
        self.assertEqual(con.execute("SELECT latest_base_count FROM ena_run_history WHERE run_accession='R2'").fetchone(),('0',))
        provenance = json.loads(con.execute('SELECT metadata_json FROM release_provenance').fetchone()[0])
        self.assertEqual(provenance['renamed_tables'],{'assembly_ena':'assembly_ena_metadata'})
        con.close()

    def test_unexpected_scientific_write_is_blocked_and_entire_migration_rolls_back(self):
        con = self.fixture()
        con.execute("CREATE TRIGGER unexpected AFTER INSERT ON release_provenance BEGIN UPDATE assembly SET hq_filter='PASS'; END")
        with self.assertRaises(sqlite3.DatabaseError):
            transform(con,{})
        self.assertEqual(con.execute('SELECT hq_filter FROM assembly').fetchone(),('RUN_REMOVED',))
        self.assertEqual(con.execute('SELECT count(*) FROM ena_latest').fetchone(),(2,))
        self.assertEqual(con.execute('SELECT count(*) FROM assembly_ena').fetchone(),(2,))
        self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='ena_run_history'").fetchone())
        con.close()
