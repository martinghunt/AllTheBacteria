import hashlib
import csv
import gzip
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.aws_file_index import (
    Client, add_known_fastas, add_lexicmap_checksums, exclude_automated_inventory, md5_line, object_url, old_match, parse_listing, parse_md5, write_catalogues,
)


class AWSFileIndexTests(unittest.TestCase):
    def test_inventory_exclusion_is_scoped_to_metadata_bucket_and_exact_prefix(self):
        con=sqlite3.connect(':memory:')
        con.execute('CREATE TABLE objects(bucket TEXT,key TEXT)')
        prefix='allthebacteria-assemblies/assemblies-list/'
        rows=[('allthebacteria-metadata',prefix+'data/file.csv.gz'),
              ('allthebacteria-metadata',prefix+'date/manifest.json'),
              ('allthebacteria-metadata','release.1/metadata.tsv.gz'),
              ('allthebacteria-metadata',prefix.rstrip('/')+'-other/file'),
              ('allthebacteria-assemblies','SAMN1.fa.gz'),
              ('other-bucket',prefix+'file')]
        con.executemany('INSERT INTO objects VALUES(?,?)',rows)
        self.assertEqual(exclude_automated_inventory(con),2)
        self.assertEqual(set(con.execute('SELECT * FROM objects')),set(rows[2:]))
        con.close()

    def test_lexicmap_requires_complete_2024_coverage_and_ignores_running_2025(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'checksums.md5'
            con=sqlite3.connect(':memory:')
            con.execute('CREATE TABLE objects(bucket TEXT,key TEXT,md5 TEXT,md5_source TEXT)')
            con.execute("INSERT INTO objects VALUES('allthebacteria-lexicmap','202408/genomes/file',NULL,NULL)")
            p.write_text('a'*32+'  /original/2024-08/genomes/file\n'+'b'*32+'  /original/2024-08/README.md\n'+'partial  /original/2025-05/file')
            result=add_lexicmap_checksums(con,p)
            self.assertEqual(result['matched_objects'],1)
            self.assertEqual(result['unlisted_keys'],['202408/README.md'])
            self.assertEqual(con.execute('SELECT md5 FROM objects').fetchone()[0],'a'*32)
            p.write_text('b'*32+'  /original/2024-08/README.md\n')
            with self.assertRaisesRegex(ValueError,'Missing LexicMap'):
                add_lexicmap_checksums(con,p)
            con.close()

    def test_cli_catalogue_separates_fastas_and_preserves_exact_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            con=sqlite3.connect(':memory:')
            con.execute('CREATE TABLE objects(bucket TEXT,key TEXT,size INTEGER,md5 TEXT)')
            con.executemany('INSERT INTO objects VALUES(?,?,?,?)',[
                ('allthebacteria-agc','release.1/a + b.agc',1048577,'a'*32),
                ('allthebacteria-assemblies','SAMN1.fa.gz',123,None)])
            root=Path(directory)
            report=write_catalogues(con,root)
            self.assertEqual(report['master']['rows'],1)
            self.assertEqual(report['assemblies']['rows'],1)
            for name in ('all_atb_files','assembly_files'):
                self.assertEqual((root/(name+'.tsv')).read_bytes(),gzip.decompress((root/(name+'.tsv.gz')).read_bytes()))
            with (root/'all_atb_files.tsv').open() as h:
                reader=csv.DictReader(h,delimiter='\t');rows=list(reader)
                self.assertEqual(reader.fieldnames,['filename','url','md5','size_bytes','size(MB)','bucket'])
            self.assertEqual(rows[0]['size_bytes'],'1048577')
            self.assertEqual(rows[0]['size(MB)'],'1.00')
            self.assertTrue(rows[0]['url'].endswith('a%20%2B%20b.agc'))
            with (root/'assembly_files.tsv').open() as h:
                self.assertEqual(next(csv.DictReader(h,delimiter='\t'))['md5'],'NA')
            con.close()

    def test_listing_preserves_spaces_and_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'bucket.list'
            p.write_text('2026-11-11 10:00:00 123 dir/a + b.tsv.gz\n')
            self.assertEqual(list(parse_listing(p)),[('dir/a + b.tsv.gz',123,'2026-11-11 10:00:00')])
            p.write_text(p.read_text()*2)
            with self.assertRaisesRegex(ValueError,'Duplicate'):list(parse_listing(p))

    def test_md5_manifest_round_trip_and_rejects_multipart_etags(self):
        checksum=hashlib.md5(b'example').hexdigest()
        for key in ('dir/a + b.tsv.gz',r'dir/a\b.tsv.gz'):
            self.assertEqual(parse_md5(md5_line(checksum,key).encode()),{key:checksum})
        with self.assertRaises(ValueError):parse_md5((checksum+'-2  file\n').encode())
        with self.assertRaises(ValueError):md5_line(checksum,'../file')

    def test_ambiguous_basename_never_picks_an_arbitrary_checksum(self):
        rows=[{'project':'AllTheBacteria/Metadata','filename':name,'md5':checksum,'size(MB)':'1','url':name}
              for name,checksum in [('old/a.tsv','a'*32),('other/a.tsv','b'*32)]]
        self.assertIsNone(old_match('allthebacteria-metadata','new/a.tsv',1048576,rows))
        self.assertEqual(old_match('allthebacteria-metadata','old/a.tsv',1048576,rows)[0],'a'*32)

    def test_exact_path_size_mismatch_does_not_fall_back_to_a_different_file(self):
        rows=[{'project':'AllTheBacteria/Metadata','filename':name,'md5':checksum,'size(MB)':size,'url':name}
              for name,checksum,size in [('old/a.tsv','a'*32,'2'),('other/a.tsv','b'*32,'1')]]
        self.assertIsNone(old_match('allthebacteria-metadata','old/a.tsv',1048576,rows))
        self.assertIsNone(old_match('allthebacteria-amr','old/a.tsv',2097152,rows))

    def test_cached_payload_is_checked_and_url_escapes_object_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=Client(root)
            url=object_url('allthebacteria-metadata','dir/a + b.tsv')
            self.assertTrue(url.endswith('/dir/a%20%2B%20b.tsv'))
            p=root/(hashlib.sha256(url.encode()).hexdigest()+'.data')
            p.write_bytes(b'example')
            self.assertEqual(client.get('allthebacteria-metadata','dir/a + b.tsv',7,hashlib.md5(b'example').hexdigest()),p)
            with self.assertRaisesRegex(ValueError,'MD5 mismatch'):
                client.get('allthebacteria-metadata','dir/a + b.tsv',7,'0'*32)

    def test_known_fastas_only_supplement_inventory_and_leave_source_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'source.sqlite'
            source=sqlite3.connect(p)
            source.execute('CREATE TABLE assembly(sample_accession TEXT,aws_url TEXT)')
            source.executemany('INSERT INTO assembly VALUES(?,?)',[
                ('SAMN1',object_url('allthebacteria-assemblies','SAMN1.fa.gz')),
                ('SAMN2',object_url('allthebacteria-assemblies','SAMN2.fa.gz')),
                ('SAMN3','NA'),('SAMN4',None)])
            source.commit();source.close();before=p.read_bytes()
            con=sqlite3.connect(':memory:')
            con.execute('CREATE TABLE objects(bucket TEXT,key TEXT,size INTEGER,listing_source TEXT,PRIMARY KEY(bucket,key))')
            con.execute("INSERT INTO objects VALUES('allthebacteria-assemblies','SAMN1.fa.gz',123,'s3_inventory')")
            result=add_known_fastas(con,p)
            self.assertEqual(result,{'known_urls':2,'additional_objects':1,
                'policy':'Only actual non-missing AWS URLs are used. No bucket listing or per-genome requests.'})
            self.assertEqual(con.execute("SELECT size,listing_source FROM objects WHERE key='SAMN1.fa.gz'").fetchone(),(123,'s3_inventory'))
            self.assertEqual(con.execute('SELECT count(*) FROM objects').fetchone()[0],2)
            self.assertEqual(p.read_bytes(),before)
            con.close()
