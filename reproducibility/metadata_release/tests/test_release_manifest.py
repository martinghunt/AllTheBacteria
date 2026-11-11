import csv
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.release_manifest import export_sample_run_base_count


class ReleaseManifestTest(unittest.TestCase):
    def test_exports_only_pipeline_pass_rows_in_assembly_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assembly = root / "assembly.tsv"
            assembly.write_text(
                "sample_accession\trun_accession\tdataset\tasm_pipe_filter\n"
                "S2\tR2\tIncr_release.202603\tPASS\n"
                "S1\tR1\tIncr_release.202603\tFASTQ_MD5_CHANGED\n"
                "S3\tR3\tolder\tPASS\n"
            )
            ena = root / "ena.tsv"
            ena.write_text(
                "run_accession\tbase_count\tatb_present_in_current_snapshot\n"
                "R2\t200\t1\nR1\t100\t1\nR3\t300\t1\n"
            )
            output = root / "manifest.tsv"
            result = export_sample_run_base_count(assembly, ena, output, "Incr_release.202603")
            with output.open() as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(result["row_count"], 1)
            self.assertEqual(rows, [{"sample_accession": "S2", "run_accession": "R2", "base_count": "200"}])
            output.unlink()
            assembly.write_text(
                "sample_accession\trun_accession\tdataset\tasm_pipe_filter\tin_release_1\n"
                "S2\tR2\tIncr_release.202603\tENA_ASM_SUBMIT_ERR\t1\n"
                "S1\tR1\tIncr_release.202603\tPASS\t0\n"
            )
            export_sample_run_base_count(assembly, ena, output, "Incr_release.202603")
            with output.open() as handle:
                frozen_rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(frozen_rows, rows)


if __name__ == "__main__":
    unittest.main()
