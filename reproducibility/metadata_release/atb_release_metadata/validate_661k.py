from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from .io import iter_tsv_dicts, read_header, sha256_file


class ValidationError(RuntimeError):
    """Raised when the published 661k sources do not reconcile exactly."""


def _sample_ids(path: Path) -> set[str]:
    header = read_header(path)
    if "sample_id" not in header:
        raise ValidationError(f"{path}: missing sample_id")
    result: set[str] = set()
    for line_number, row in iter_tsv_dicts(path):
        sample = row["sample_id"]
        if not sample:
            raise ValidationError(f"{path}:{line_number}: empty sample_id")
        if sample in result:
            raise ValidationError(f"{path}:{line_number}: duplicate sample_id {sample}")
        result.add(sample)
    return result


def validate(
    json_path: Path,
    file3_path: Path,
    file9_path: Path,
    ena_table_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    if report_path.exists():
        raise ValidationError(f"refusing to overwrite report: {report_path}")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with gzip.open(json_path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValidationError(f"{json_path}: top-level JSON must be an object")

    table_columns = read_header(ena_table_path)
    table_column_set = set(table_columns)
    if "run_accession" not in table_column_set or "sample_accession" not in table_column_set:
        raise ValidationError(f"{ena_table_path}: missing run or sample accession column")

    json_runs: dict[str, dict[str, str]] = {}
    multi_run_samples = 0
    maximum_runs = 0
    for sample, sample_data in data.items():
        if not isinstance(sample_data, dict):
            raise ValidationError(f"{json_path}: sample {sample} is not an object")
        records = sample_data.get("ena_metadata")
        if not isinstance(records, list):
            raise ValidationError(f"{json_path}: sample {sample} has no ena_metadata list")
        maximum_runs = max(maximum_runs, len(records))
        multi_run_samples += int(len(records) > 1)
        for index, row_value in enumerate(records):
            if not isinstance(row_value, dict):
                raise ValidationError(
                    f"{json_path}: sample {sample} ena_metadata[{index}] is not an object"
                )
            row = {str(key): str(value) for key, value in row_value.items()}
            if set(row) != table_column_set:
                missing = sorted(table_column_set.difference(row))
                extra = sorted(set(row).difference(table_column_set))
                raise ValidationError(
                    f"{json_path}: {sample} ena_metadata[{index}] schema differs; "
                    f"missing={missing}, extra={extra}"
                )
            run = row["run_accession"]
            if row["sample_accession"] != sample:
                raise ValidationError(
                    f"{json_path}: run {run} says {row['sample_accession']}, object key is {sample}"
                )
            if not run or run in json_runs:
                raise ValidationError(f"{json_path}: empty or duplicate run_accession {run!r}")
            json_runs[run] = row

    json_samples = set(data)
    for label, path in (("File3", file3_path), ("File9", file9_path)):
        observed = _sample_ids(path)
        if observed != json_samples:
            missing = sorted(json_samples.difference(observed))
            extra = sorted(observed.difference(json_samples))
            raise ValidationError(
                f"{label} sample census differs from JSON: "
                f"missing={missing[:5]} ({len(missing)} total), "
                f"extra={extra[:5]} ({len(extra)} total)"
            )

    table_runs: set[str] = set()
    for line_number, row in iter_tsv_dicts(ena_table_path):
        run = row["run_accession"]
        if run in table_runs:
            raise ValidationError(f"{ena_table_path}:{line_number}: duplicate run {run}")
        table_runs.add(run)
        expected = json_runs.get(run)
        if expected is None:
            raise ValidationError(f"{ena_table_path}:{line_number}: run {run} is not in JSON")
        differences = [column for column in table_columns if row[column] != expected[column]]
        if differences:
            column = differences[0]
            raise ValidationError(
                f"{ena_table_path}:{line_number}: run {run} differs in {column}: "
                f"table={row[column]!r}, JSON={expected[column]!r}"
            )
    missing_runs = sorted(set(json_runs).difference(table_runs))
    if missing_runs:
        raise ValidationError(
            f"{ena_table_path}: missing {len(missing_runs)} JSON runs; first is {missing_runs[0]}"
        )

    result: dict[str, Any] = {
        "status": "PASS",
        "sample_count": len(json_samples),
        "selected_run_count": len(json_runs),
        "multi_run_sample_count": multi_run_samples,
        "maximum_selected_runs_for_one_sample": maximum_runs,
        "inputs": {
            "Json1_ENA_metadata": {"path": str(json_path.resolve()), "sha256": sha256_file(json_path)},
            "File3_metadata_661K": {"path": str(file3_path.resolve()), "sha256": sha256_file(file3_path)},
            "File9_all_metadata_ena_661K": {"path": str(file9_path.resolve()), "sha256": sha256_file(file9_path)},
            "ena_661k_table": {"path": str(ena_table_path.resolve()), "sha256": sha256_file(ena_table_path)},
        },
    }
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
