import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.publication import publish


class PublicationTest(unittest.TestCase):
    def test_reconcile_membership_and_preserve_historical_osf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {k: root / (k + '.tsv') for k in ('assembly', 'mapping', 'batches', 'md5', 'output', 'archives', 'report')}
            paths['assembly'].write_text('sample_accession\tin_release_1\taws_url\tdataset\tosf_tarball_filename\tosf_tarball_url\nS1\t1\tNA\t661k\told.tar.xz\thttps://osf.io/old\nS2\t0\tTODO\tnew\tTODO\tTODO\n')
            paths['mapping'].write_text('accession\tbatch_name\nS1\tatb.assembly.r1.part00001\n')
            paths['batches'].write_text('batch_name\tgenome_count\tsize_bytes\natb.assembly.r1.part00001\t1\t123\n')
            paths['md5'].write_text('0123456789abcdef0123456789abcdef  atb.assembly.r1.part00001.agc\n')
            result = publish(**paths)
            self.assertEqual(result['release_1_rows'], 1)
            self.assertIn('https://osf.io/old', paths['output'].read_text())
            self.assertIn('S1.fa.gz', paths['output'].read_text())
            paths['output'].unlink()
            paths['archives'].unlink()
            paths['report'].unlink()
            paths['mapping'].write_text('accession\tbatch_name\nS2\tatb.assembly.r1.part00001\n')
            with self.assertRaisesRegex(ValueError, 'unmapped'):
                publish(**paths)
