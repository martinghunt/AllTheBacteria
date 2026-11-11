import csv
import json
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.historical_gaps import export_historical_assembly_run_gaps


class HistoricalGapTest(unittest.TestCase):
    def test_export_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assembly = root / "assembly.tsv"
            assembly.write_text(
                "sample_accession\trun_accession\tassembly_accession\tasm_pipe_filter\t"
                "hq_filter\tdataset\tcomments\n"
                "S2\tR2\tNA\tRUN_REMOVED\tRUN_REMOVED\tr0.2\tremoved\n"
                "S1\tR1\tNA\tPASS\tPASS\tr0.2\tkept\n"
            )
            report = root / "report.json"
            report.write_text(json.dumps({"historical_assembly_runs_absent_from_run_table": ["R2"]}))
            output = root / "gaps.tsv"
            result = export_historical_assembly_run_gaps(assembly, report, output)
            with output.open() as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(result["row_count"], 1)
            self.assertEqual(rows[0]["sample_accession"], "S2")
            self.assertEqual(rows[0]["run_accession"], "R2")


if __name__ == "__main__":
    unittest.main()
