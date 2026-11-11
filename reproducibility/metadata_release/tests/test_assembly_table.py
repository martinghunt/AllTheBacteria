import unittest

from atb_release_metadata.assembly_table import DUP_SEQKIT_SUM, PROCESS_METADATA_CHANGE_FILTERS, _format_filters, _hq_filter, _legacy_in_release_1, _natural_accession, _split_runs


class AssemblyTableUnitTest(unittest.TestCase):
    def test_filter_order_is_deterministic(self):
        self.assertEqual(_format_filters({"RUN_REMOVED", "NO_RUNS"}), "NO_RUNS,RUN_REMOVED")
        self.assertEqual(_format_filters(set()), "PASS")

    def test_hq_thresholds_are_inclusive(self):
        stats = {"total_length": 100000, "number": 2000, "n50": 2000}
        checkm = {"completeness": "90", "contamination": "5"}
        self.assertEqual(_hq_filter(set(), "PASS", stats, checkm), "PASS")
        stats["total_length"] = 99999
        self.assertEqual(_hq_filter(set(), "PASS", stats, checkm), "ASM_MIN_LEN")

    def test_checkm_na_means_no_diamond_result(self):
        stats = {"total_length": 100000, "number": 1, "n50": 100000}
        checkm = {"completeness": "NA", "contamination": "NA"}
        self.assertEqual(_hq_filter(set(), "PASS", stats, checkm), "CHECKM2_NO_DIAMOND")

    def test_legacy_missing_run_sentinels_are_not_accessions(self):
        self.assertEqual(_split_runs("NA"), [])
        self.assertEqual(_split_runs("unknown"), [])

    def test_duplicate_filter_is_retained_when_hq_metrics_are_unavailable(self):
        self.assertEqual(
            _hq_filter(set(), "PASS", None, None, {DUP_SEQKIT_SUM}),
            DUP_SEQKIT_SUM,
        )

    def test_sample_accession_order_is_natural(self):
        self.assertGreater(_natural_accession("SAMEA10"), _natural_accession("SAMEA9"))

    def test_process_time_fastq_change_filters_are_explicit(self):
        self.assertEqual(PROCESS_METADATA_CHANGE_FILTERS["fastq_md5"], "FASTQ_MD5_CHANGED")
        self.assertEqual(PROCESS_METADATA_CHANGE_FILTERS["fastq_bytes"], "FASTQ_BYTES_CHANGED")

    def test_release_1_retains_published_legacy_artifacts_except_duplicate_nonkeepers(self):
        self.assertEqual(_legacy_in_release_1("r0.2", "1", "NONE"), "1")
        self.assertEqual(_legacy_in_release_1("r0.2", "1", "LIKELY_DUPLICATE"), "0")
        self.assertEqual(_legacy_in_release_1("r0.2", "0", "NONE"), "0")
        self.assertEqual(_legacy_in_release_1("not_processed", "1", "NONE"), "0")


if __name__ == "__main__":
    unittest.main()
