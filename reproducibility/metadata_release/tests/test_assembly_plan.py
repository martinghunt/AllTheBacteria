import csv
import json
import lzma
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.assembly_plan import AssemblyPlanPaths, make_assembly_plan


class AssemblyPlanTest(unittest.TestCase):
    def test_old_samples_and_multiple_passing_runs_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.tsv"
            old.write_text("sample_accession\nS1\n")
            runs = root / "runs.tsv"
            runs.write_text(
                "run_accession\tsample_accession\tpass\n"
                "R1\tS1\t1\nR2\tS2\t1\nR3\tS3\t1\nR4\tS3\t1\nR5\tS4\t0\n"
            )
            executed = root / "executed.txt"
            executed.write_text("S2 R2\nS5 R6\n")
            output = root / "out.tsv.xz"
            report_path = root / "report.json"
            result = make_assembly_plan(
                old, runs,
                AssemblyPlanPaths(root / "work.sqlite", output, report_path),
                executed,
            )
            with lzma.open(output, "rt") as handle:
                self.assertEqual(list(csv.reader(handle, delimiter="\t")), [
                    ["sample_accession", "run_accession"], ["S2", "R2"]
                ])
            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["comparison_to_executed"]["executed_only_samples"], ["S5"])
            self.assertEqual(result["new_sample_passing_run_multiplicity"], {"1": 1, "2": 1})


if __name__ == "__main__":
    unittest.main()
