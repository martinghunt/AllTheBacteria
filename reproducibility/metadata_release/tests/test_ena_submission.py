import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.ena_submission import apply
from atb_release_metadata.io import iter_tsv_dicts


class SubmissionTest(unittest.TestCase):
    def test_failure_retains_membership_hq_and_prior_comment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assembly, status, output, report = [root / name for name in ('a.tsv', 's.tsv', 'o.tsv', 'r.json')]
            assembly.write_text('sample_accession\tdataset\tin_release_1\tassembly_accession\tasm_pipe_filter\thq_filter\tcomments\nS1\tIncr_release.202603\t1\tTODO\tPASS\tPASS\told note\nS2\tIncr_release.202603\t1\tTODO\tPASS\tPASS\tNone\n')
            status.write_text('Sample\tStatus\tAccession\nS1\terror_1_contig\tNone\nS2\tsuccess\tERZ123\n')
            apply(assembly, status, output, report)
            rows = [row for _, row in iter_tsv_dicts(output)]
            self.assertEqual(rows[0]['in_release_1'], '1')
            self.assertEqual(rows[0]['hq_filter'], 'PASS')
            self.assertEqual(rows[0]['asm_pipe_filter'], 'ENA_ASM_SUBMIT_ERR')
            self.assertEqual(rows[0]['comments'], 'old note; ENA assembly submission error: error_1_contig')
            self.assertEqual(rows[1]['assembly_accession'], 'ERZ123')
            output.unlink(); report.unlink()
            status.write_text('Sample\tStatus\tAccession\nS1\tsuccess\tERZ123\n')
            with self.assertRaisesRegex(ValueError, 'sets differ'):
                apply(assembly, status, output, report)
