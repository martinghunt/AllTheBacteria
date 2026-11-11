import csv
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.duplicate_assemblies import export_duplicate_assembly_report


class DuplicateAssemblyTest(unittest.TestCase):
    def test_selects_earlier_public_accession(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assembly = root / "assembly.tsv"
            assembly.write_text(
                "sample_accession\trun_accession\tassembly_seqkit_sum\tdataset\tasm_pipe_filter\thq_filter\n"
                "S1\tERR1\thash\tIncr_release.202603\tPASS\tPASS\n"
                "S2\tERR2\thash\tIncr_release.202603\tPASS\tPASS\n"
            )
            ena = root / "ena.tsv"
            ena.write_text(
                "run_accession\tsample_accession\tfirst_public\tfirst_created\trun_date\tfastq_ftp\tfastq_md5\tfastq_bytes\tbase_count\tread_count\tatb_present_in_current_snapshot\n"
                "ERR1\tS1\t2024-01-01\t2024-01-01\t\tf1\tm1\t1\t2\t3\t1\n"
                "ERR2\tS2\t2024-02-01\t2024-02-01\t\tf2\tm2\t1\t2\t3\t1\n"
            )
            output = root / "out.tsv"
            result = export_duplicate_assembly_report(assembly, ena, output, "Incr_release.202603")
            with output.open() as handle:
                rows = {row["run_accession"]: row for row in csv.DictReader(handle, delimiter="\t")}
            self.assertEqual(result["duplicate_group_count"], 1)
            self.assertEqual(rows["ERR1"]["duplicate_status"], "KEEP_OLDEST")
            self.assertEqual(rows["ERR2"]["duplicate_status"], "LIKELY_DUPLICATE")


if __name__ == "__main__":
    unittest.main()
