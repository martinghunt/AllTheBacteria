import csv
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.duplicate_metadata_comparison import compare_duplicate_ena_metadata


class DuplicateMetadataComparisonTest(unittest.TestCase):
    def test_flags_selected_1905_with_non_1905_alternative(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "duplicates.tsv"
            report.write_text(
                "seqkit_sum\tgroup_size\tsample_accession\trun_accession\tduplicate_status\tfirst_public\n"
                "hash\t2\tS1\tR1\tKEEP_OLDEST\t2024-01-01\n"
                "hash\t2\tS2\tR2\tLIKELY_DUPLICATE\t2024-02-01\n"
            )
            metadata = root / "metadata.tsv"
            metadata.write_text(
                "run_accession\tsample_accession\tstudy_accession\tsecondary_study_accession\tdescription\t"
                "instrument_platform\tlibrary_layout\tcenter_name\tbroker_name\tread_count\tbase_count\tcollection_date\tscientific_name\n"
                "R1\tS1\tP1\tD1\ta\tILLUMINA\tPAIRED\tc\tb\t10\t20\t1905-07-01\tSpecies\n"
                "R2\tS2\tP2\tD2\ta\tILLUMINA\tPAIRED\tc\tb\t10\t20\t2020-01-01\tSpecies\n"
            )
            output = root / "comparison.tsv"
            result = compare_duplicate_ena_metadata(report, metadata, output)
            with output.open() as handle:
                row = next(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(result["collection_date_group_counts"], {"SELECTED_1905_HAS_NON_1905_ALTERNATIVE": 1})
            self.assertEqual(row["collection_date_class"], "SELECTED_1905_HAS_NON_1905_ALTERNATIVE")
            self.assertIn("study_accession", row["different_fields"])


if __name__ == "__main__":
    unittest.main()
