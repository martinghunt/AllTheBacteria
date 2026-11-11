from __future__ import annotations

import csv
import gzip
import json
import lzma
import tempfile
import unittest
from pathlib import Path

from atb_release_metadata.config import load_config
from atb_release_metadata.ena_latest import ATB_COLUMNS, BuildError, BuildPaths, build
from atb_release_metadata.io import TsvFormatError, iter_tsv_dicts
from atb_release_metadata.run_table import OUTPUT_COLUMNS, RunBuildPaths, build_run_table
from atb_release_metadata.validate_661k import validate as validate_661k


CORE = [
    "run_accession",
    "sample_accession",
    "instrument_platform",
    "library_strategy",
    "library_source",
    "library_layout",
    "fastq_ftp",
    "fastq_md5",
    "fastq_bytes",
    "base_count",
    "read_count",
    "scientific_name",
]


def write_tsv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(
            handle,
            delimiter="\t",
            lineterminator="\n",
            quotechar=None,
            quoting=csv.QUOTE_NONE,
        )
        writer.writerow(header)
        writer.writerows(rows)


def core_row(run: str, sample: str, md5: str, extra: str | None = None) -> list[str]:
    row = [
        run,
        sample,
        "ILLUMINA",
        "WGS",
        "GENOMIC",
        "PAIRED",
        f"ftp/{run}_1.fastq.gz;ftp/{run}_2.fastq.gz",
        md5,
        "100;100",
        "200",
        "2",
        "Example species",
    ]
    if extra is not None:
        row.append(extra)
    return row


class EnaLatestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def config(self, sources: list[dict[str, object]], previous: str | None = None) -> Path:
        data: dict[str, object] = {
            "release": "test",
            "current_source": sources[-1]["name"],
            "sources": sources,
        }
        if previous is not None:
            data["previous_latest"] = previous
        path = self.root / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def outputs(self, stem: str = "result") -> BuildPaths:
        return BuildPaths(
            database=self.root / f"{stem}.sqlite",
            output=self.root / f"{stem}.tsv.xz",
            changes=self.root / f"{stem}.changes.tsv.xz",
            manifest=self.root / f"{stem}.manifest.json",
        )

    @staticmethod
    def read_rows(path: Path) -> list[dict[str, str]]:
        with lzma.open(path, "rt", encoding="utf-8") as handle:
            return list(csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))

    def test_schema_union_latest_record_and_removed_run(self) -> None:
        old = self.root / "old.tsv"
        new = self.root / "new.tsv"
        write_tsv(
            old,
            [*CORE, "historical_only"],
            [core_row("RUN1", "SAMPLE1", "a;b", "old"), core_row("RUN2", "SAMPLE2", "c;d", "kept")],
        )
        reordered = ["new_column", *reversed(CORE)]
        new_values = dict(zip(CORE, core_row("RUN1", "SAMPLE1", "a;b"), strict=True))
        write_tsv(new, reordered, [["new", *[new_values[x] for x in reversed(CORE)]]])
        config = self.config(
            [
                {"name": "old", "path": "old.tsv", "date": "2025-01-01", "kind": "complete", "precedence": 10},
                {"name": "new", "path": "new.tsv", "date": "2026-01-01", "kind": "complete", "precedence": 20},
            ]
        )
        paths = self.outputs()
        build(load_config(config), paths)
        rows = self.read_rows(paths.output)
        self.assertEqual([row["run_accession"] for row in rows], ["RUN1", "RUN2"])
        run1, run2 = rows
        self.assertEqual(run1["new_column"], "new")
        self.assertEqual(run1["historical_only"], "")
        self.assertEqual(run2["historical_only"], "kept")
        self.assertEqual(run2["new_column"], "")
        self.assertEqual(run1["atb_present_in_current_snapshot"], "1")
        self.assertEqual(run2["atb_present_in_current_snapshot"], "0")
        changes = self.read_rows(paths.changes)
        self.assertEqual(
            {(row["run_accession"], row["change_type"]) for row in changes},
            {("RUN2", "RUN_REMOVED")},
        )

    def test_changes_are_reported_and_multisample_row_is_retained(self) -> None:
        old = self.root / "old.tsv"
        new = self.root / "new.tsv"
        write_tsv(old, CORE, [core_row("RUN1", "SAMPLE1", "a;b")])
        write_tsv(new, CORE, [core_row("RUN1", "SAMPLE2;SAMPLE3", "a;changed")])
        config = self.config(
            [
                {"name": "old", "path": "old.tsv", "date": "2025-01-01", "kind": "complete", "precedence": 10},
                {"name": "new", "path": "new.tsv", "date": "2026-01-01", "kind": "complete", "precedence": 20},
            ]
        )
        paths = self.outputs()
        build(load_config(config), paths)
        row = self.read_rows(paths.output)[0]
        self.assertEqual(row["sample_accession"], "SAMPLE2;SAMPLE3")
        self.assertEqual(row["atb_ever_sample_accession_changed"], "1")
        self.assertEqual(row["atb_ever_fastq_md5_changed"], "1")
        self.assertEqual(
            {row["change_type"] for row in self.read_rows(paths.changes)},
            {"SAMPLE_ACCESSION_CHANGED", "FASTQ_MD5_CHANGED"},
        )

    def test_per_row_date_and_explicit_precedence(self) -> None:
        partial = self.root / "partial.tsv"
        complete = self.root / "complete.tsv"
        write_tsv(
            partial,
            ["obtained_date", *CORE, "partial_only"],
            [["20260101", *core_row("RUN1", "SAMPLE1", "old;old"), "yes"]],
        )
        write_tsv(complete, CORE, [core_row("RUN1", "SAMPLE1", "new;new")])
        config = self.config(
            [
                {
                    "name": "partial",
                    "path": "partial.tsv",
                    "date_column": "obtained_date",
                    "exclude_columns": ["obtained_date"],
                    "kind": "partial",
                    "precedence": 10,
                },
                {
                    "name": "complete",
                    "path": "complete.tsv",
                    "date": "2026-01-01",
                    "kind": "complete",
                    "precedence": 20,
                },
            ]
        )
        paths = self.outputs()
        build(load_config(config), paths)
        row = self.read_rows(paths.output)[0]
        self.assertEqual(row["fastq_md5"], "new;new")
        self.assertEqual(row["partial_only"], "")
        self.assertEqual(row["atb_latest_source"], "complete")
        self.assertNotIn("obtained_date", row)

    def test_increment_from_previous_latest(self) -> None:
        old = self.root / "old.tsv"
        first = self.root / "first.tsv"
        write_tsv(old, CORE, [core_row("RUN1", "SAMPLE1", "a;b")])
        first_config = self.config(
            [{"name": "old", "path": "old.tsv", "date": "2025-01-01", "kind": "complete", "precedence": 10}]
        )
        first_paths = self.outputs("first")
        build(load_config(first_config), first_paths)

        reordered = core_row("RUN1", "SAMPLE1", "b;a")
        reordered[CORE.index("fastq_ftp")] = "ftp/RUN1_2.fastq.gz;ftp/RUN1_1.fastq.gz"
        write_tsv(self.root / "new.tsv", CORE, [reordered])
        second_config = self.config(
            [{"name": "new", "path": "new.tsv", "date": "2026-01-01", "kind": "complete", "precedence": 10}],
            previous="first.tsv.xz",
        )
        second_paths = self.outputs("second")
        build(load_config(second_config), second_paths)
        row = self.read_rows(second_paths.output)[0]
        self.assertEqual(row["atb_observation_count"], "2")
        self.assertEqual(row["atb_first_seen_date"], "2025-01-01")
        self.assertEqual(row["atb_last_seen_date"], "2026-01-01")
        self.assertEqual(row["atb_ever_fastq_md5_changed"], "0")
        self.assertEqual(self.read_rows(second_paths.changes), [])

    def test_values_swapped_between_fastq_files_are_a_change(self) -> None:
        old = self.root / "old.tsv"
        new = self.root / "new.tsv"
        write_tsv(old, CORE, [core_row("RUN1", "SAMPLE1", "a;b")])
        write_tsv(new, CORE, [core_row("RUN1", "SAMPLE1", "b;a")])
        config = self.config(
            [
                {"name": "old", "path": "old.tsv", "date": "2025-01-01", "kind": "complete", "precedence": 10},
                {"name": "new", "path": "new.tsv", "date": "2026-01-01", "kind": "complete", "precedence": 20},
            ]
        )
        paths = self.outputs()
        build(load_config(config), paths)
        self.assertEqual(
            {row["change_type"] for row in self.read_rows(paths.changes)},
            {"FASTQ_MD5_CHANGED"},
        )

    def test_duplicate_run_in_source_fails(self) -> None:
        source = self.root / "duplicate.tsv"
        write_tsv(source, CORE, [core_row("RUN1", "S1", "a;b"), core_row("RUN1", "S1", "a;b")])
        config = self.config(
            [{"name": "current", "path": "duplicate.tsv", "date": "2026-01-01", "kind": "complete", "precedence": 10}]
        )
        with self.assertRaisesRegex(BuildError, "duplicate observation.*RUN1"):
            build(load_config(config), self.outputs())

    def test_partial_source_can_observe_one_run_on_multiple_dates(self) -> None:
        partial = self.root / "partial.tsv"
        current = self.root / "current.tsv"
        write_tsv(
            partial,
            ["obtained_date", *CORE],
            [
                ["20250101", *core_row("RUN1", "SAMPLE_OLD", "a;b")],
                ["20250201", *core_row("RUN1", "SAMPLE_NEW", "a;b")],
            ],
        )
        write_tsv(current, CORE, [core_row("RUN2", "SAMPLE2", "c;d")])
        config = self.config(
            [
                {
                    "name": "partial",
                    "path": "partial.tsv",
                    "date_column": "obtained_date",
                    "kind": "partial",
                    "precedence": 10,
                },
                {
                    "name": "current",
                    "path": "current.tsv",
                    "date": "2026-01-01",
                    "kind": "complete",
                    "precedence": 20,
                },
            ]
        )
        paths = self.outputs()
        manifest = build(load_config(config), paths)
        rows = {row["run_accession"]: row for row in self.read_rows(paths.output)}
        self.assertEqual(rows["RUN1"]["sample_accession"], "SAMPLE_NEW")
        self.assertEqual(rows["RUN1"]["atb_latest_observed_date"], "2025-02-01")
        self.assertEqual(rows["RUN1"]["atb_observation_count"], "2")
        self.assertEqual(rows["RUN1"]["atb_ever_sample_accession_changed"], "1")
        partial_input = next(x for x in manifest["inputs"] if x.get("name") == "partial")
        self.assertEqual(partial_input["row_count"], 2)
        self.assertEqual(partial_input["unique_run_count"], 1)

    def test_malformed_row_fails(self) -> None:
        source = self.root / "bad.tsv"
        source.write_text("run_accession\tsample_accession\nRUN1\n", encoding="utf-8")
        with self.assertRaisesRegex(TsvFormatError, "expected 2 fields"):
            list(iter_tsv_dicts(source))

    def test_literal_quotes_round_trip_without_csv_quoting(self) -> None:
        source = self.root / "quotes.tsv"
        write_tsv(
            source,
            [*CORE, "description"],
            [[*core_row("RUN1", "SAMPLE1", "a;b"), 'literal "quoted" text']],
        )
        config = self.config(
            [
                {
                    "name": "current",
                    "path": "quotes.tsv",
                    "date": "2026-01-01",
                    "kind": "complete",
                    "precedence": 10,
                }
            ]
        )
        paths = self.outputs()
        build(load_config(config), paths)
        self.assertEqual(
            self.read_rows(paths.output)[0]["description"], 'literal "quoted" text'
        )

    def test_missing_required_current_column_fails(self) -> None:
        source = self.root / "missing.tsv"
        header = [column for column in CORE if column != "fastq_md5"]
        row = dict(zip(CORE, core_row("RUN1", "S1", "a;b"), strict=True))
        write_tsv(source, header, [[row[column] for column in header]])
        config = self.config(
            [{"name": "current", "path": "missing.tsv", "date": "2026-01-01", "kind": "complete", "precedence": 10}]
        )
        with self.assertRaisesRegex(BuildError, "fastq_md5"):
            build(load_config(config), self.outputs())

    def test_missing_critical_value_is_reported_for_new_run(self) -> None:
        source = self.root / "missing-value.tsv"
        row = core_row("RUN1", "SAMPLE1", "")
        write_tsv(source, CORE, [row])
        config = self.config(
            [
                {
                    "name": "current",
                    "path": "missing-value.tsv",
                    "date": "2026-01-01",
                    "kind": "complete",
                    "precedence": 10,
                }
            ]
        )
        paths = self.outputs()
        build(load_config(config), paths)
        changes = self.read_rows(paths.changes)
        self.assertEqual(
            {(row["change_type"], row["field"]) for row in changes},
            {("NEW_RUN", ""), ("CURRENT_VALUE_MISSING", "fastq_md5")},
        )

    def test_661k_sources_reconcile_field_for_field(self) -> None:
        json_path = self.root / "661k.json.gz"
        file3 = self.root / "File3.tsv"
        file9 = self.root / "File9.tsv"
        table = self.root / "ena_661k.tsv.xz"
        row1 = dict(zip(CORE, core_row("RUN1", "SAMPLE1", "a;b"), strict=True))
        row2 = dict(zip(CORE, core_row("RUN2", "SAMPLE1", "c;d"), strict=True))
        with gzip.open(json_path, "wt", encoding="utf-8") as handle:
            json.dump({"SAMPLE1": {"ena_metadata": [row1, row2]}}, handle)
        write_tsv(file3, ["sample_id", "assembly_status"], [["SAMPLE1", "True"]])
        write_tsv(file9, ["sample_id", "run_accession"], [["SAMPLE1", "RUN1,RUN2"]])
        with lzma.open(table, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(CORE)
            writer.writerow([row1[column] for column in CORE])
            writer.writerow([row2[column] for column in CORE])
        report = self.root / "661k-report.json"
        result = validate_661k(json_path, file3, file9, table, report)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["selected_run_count"], 2)
        self.assertEqual(result["multi_run_sample_count"], 1)

    def test_run_table_preserves_history_and_adds_current_failures(self) -> None:
        old_ena = self.root / "old-ena.tsv"
        new_ena = self.root / "new-ena.tsv"
        write_tsv(
            old_ena,
            CORE,
            [
                core_row("RUN1", "SAMPLE_OLD", "a;b"),
                core_row("RUN3", "SAMPLE3", "e;f"),
            ],
        )
        write_tsv(
            new_ena,
            CORE,
            [
                core_row("RUN1", "SAMPLE_NEW", "changed;b"),
                core_row("RUN2", "SAMPLE2", "c;d"),
            ],
        )
        ena_config = self.config(
            [
                {"name": "old", "path": "old-ena.tsv", "date": "2025-01-01", "kind": "complete", "precedence": 10},
                {"name": "new", "path": "new-ena.tsv", "date": "2026-01-01", "kind": "complete", "precedence": 20},
            ]
        )
        ena_paths = self.outputs("ena-for-run")
        build(load_config(ena_config), ena_paths)

        previous_run = self.root / "previous-run.tsv"
        previous_header = [
            "run_accession", "sample_accession", "in_661k",
            "in_ena_20240625", "in_ena_20240801", "in_ena_20250506",
            "ena_202505_batch", "fastq_md5", "meta_pass_atb",
            "meta_pass_661k", "pass", "comments",
        ]
        write_tsv(
            previous_run,
            previous_header,
            [
                ["RUN1", "SAMPLE_OLD", "0", "1", "1", "1", "NA", "a;b", "1", "1", "1", ""],
                ["RUN3", "SAMPLE3", "1", "1", "1", "1", "NA", "e;f", "1", "1", "1", ""],
            ],
        )
        run_paths = RunBuildPaths(
            self.root / "run.sqlite",
            self.root / "run.tsv.xz",
            self.root / "run-report.json",
        )
        report = build_run_table(previous_run, ena_paths.output, run_paths)
        rows = {row["run_accession"]: row for row in self.read_rows(run_paths.output)}
        self.assertEqual(set(rows), {"RUN1", "RUN2", "RUN3"})
        self.assertEqual(list(rows["RUN1"]), list(OUTPUT_COLUMNS))
        self.assertEqual(rows["RUN1"]["sample_accession"], "SAMPLE_NEW,SAMPLE_OLD")
        self.assertEqual(rows["RUN1"]["fastq_md5"], "multiple")
        self.assertEqual(rows["RUN1"]["pass"], "0")
        self.assertIn("SAMPLE_ACCESSION_CHANGED", rows["RUN1"]["failure_reasons"])
        self.assertEqual(rows["RUN2"]["pass"], "1")
        self.assertEqual(rows["RUN3"]["pass"], "0")
        self.assertIn("NOT_IN_CURRENT_ENA", rows["RUN3"]["failure_reasons"])
        self.assertEqual(report["previous_run_count"], 2)
        self.assertEqual(report["new_run_count"], 1)
        self.assertEqual(report["output_run_count"], 3)

        future_paths = RunBuildPaths(
            self.root / "future-run.sqlite",
            self.root / "future-run.tsv.xz",
            self.root / "future-run-report.json",
        )
        build_run_table(
            run_paths.output, ena_paths.output, future_paths,
            current_snapshot_column="in_ena_20270101", release="202701",
        )
        future = {row["run_accession"]: row for row in self.read_rows(future_paths.output)}
        self.assertIn("in_ena_20260317", future["RUN2"])
        self.assertIn("in_ena_20270101", future["RUN2"])
        self.assertEqual(future["RUN2"]["in_ena_20260317"], "1")


if __name__ == "__main__":
    unittest.main()
