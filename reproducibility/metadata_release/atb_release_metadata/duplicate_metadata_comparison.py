from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .io import iter_tsv_dicts, open_tsv_writer, read_header, sha256_file


COMPARE_FIELDS = (
    "study_accession", "secondary_study_accession", "description",
    "instrument_platform", "library_layout", "center_name", "broker_name",
    "read_count", "base_count", "collection_date", "scientific_name",
)
OUTPUT_COLUMNS = (
    "seqkit_sum", "group_size", "keep_oldest_sample_accession",
    "keep_oldest_run_accession", "keep_oldest_first_public",
    "keep_oldest_collection_date", "keep_oldest_collection_date_class",
    "collection_date_class", "collection_date_values", "different_fields",
    "different_field_values", "group_members",
)


class DuplicateMetadataComparisonError(RuntimeError):
    """Raised when an enriched duplicate-metadata file is inconsistent."""


def _collection_date_class(value: str) -> str:
    if not value or value in {"NA", "miss", "not provided"}:
        return "MISSING_OR_TEXT"
    if value.startswith("1905-"):
        return "YEAR_1905"
    return "NON_1905"


def _group_collection_date_class(values: set[str], selected_date: str) -> str:
    classes = {_collection_date_class(value) for value in values}
    selected_class = _collection_date_class(selected_date)
    if classes == {"YEAR_1905"}:
        return "ALL_1905"
    if selected_class == "YEAR_1905" and "NON_1905" in classes:
        return "SELECTED_1905_HAS_NON_1905_ALTERNATIVE"
    if selected_class == "NON_1905" and "YEAR_1905" in classes:
        return "SELECTED_NON_1905_HAS_1905_ALTERNATIVE"
    if len(values) == 1:
        return "ALL_SAME"
    return "OTHER_DIFFERENCE"


def compare_duplicate_ena_metadata(
    duplicate_report: Path, ena_metadata: Path, output: Path
) -> dict[str, object]:
    if output.exists():
        raise DuplicateMetadataComparisonError(f"refusing to overwrite output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    report_required = {
        "seqkit_sum", "group_size", "sample_accession", "run_accession",
        "duplicate_status", "first_public",
    }
    metadata_required = {"run_accession", "sample_accession", *COMPARE_FIELDS}
    for path, required in ((duplicate_report, report_required), (ena_metadata, metadata_required)):
        missing = required.difference(read_header(path))
        if missing:
            raise DuplicateMetadataComparisonError(f"{path}: missing columns: {', '.join(sorted(missing))}")

    report_rows: dict[str, dict[str, str]] = {}
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for line_number, row in iter_tsv_dicts(duplicate_report):
        run = row["run_accession"]
        if run in report_rows:
            raise DuplicateMetadataComparisonError(
                f"{duplicate_report}:{line_number}: duplicate run {run}"
            )
        report_rows[run] = row
        groups[row["seqkit_sum"]].append(row)

    metadata_rows: dict[str, dict[str, str]] = {}
    for line_number, row in iter_tsv_dicts(ena_metadata):
        run = row["run_accession"]
        if run in metadata_rows:
            raise DuplicateMetadataComparisonError(
                f"{ena_metadata}:{line_number}: duplicate run {run}"
            )
        metadata_rows[run] = row
    if set(report_rows) != set(metadata_rows):
        only_report = sorted(set(report_rows) - set(metadata_rows))
        only_metadata = sorted(set(metadata_rows) - set(report_rows))
        raise DuplicateMetadataComparisonError(
            "run sets differ: "
            f"{len(only_report)} only in duplicate report, "
            f"{len(only_metadata)} only in metadata"
        )

    comparison_rows: list[dict[str, str]] = []
    class_counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    for seqkit_sum, members in groups.items():
        keep = [row for row in members if row["duplicate_status"] == "KEEP_OLDEST"]
        if len(keep) != 1:
            raise DuplicateMetadataComparisonError(
                f"{seqkit_sum}: expected exactly one KEEP_OLDEST, found {len(keep)}"
            )
        selected = keep[0]
        selected_metadata = metadata_rows[selected["run_accession"]]
        values_by_field = {
            field: sorted({metadata_rows[row["run_accession"]][field] for row in members})
            for field in COMPARE_FIELDS
        }
        different = [field for field, values in values_by_field.items() if len(values) > 1]
        field_counts.update(different)
        dates = set(values_by_field["collection_date"])
        date_class = _group_collection_date_class(dates, selected_metadata["collection_date"])
        class_counts[date_class] += 1
        group_members = [
            {
                "sample_accession": row["sample_accession"],
                "run_accession": row["run_accession"],
                "duplicate_status": row["duplicate_status"],
                "collection_date": metadata_rows[row["run_accession"]]["collection_date"],
            }
            for row in sorted(members, key=lambda row: row["run_accession"])
        ]
        comparison_rows.append(
            {
                "seqkit_sum": seqkit_sum,
                "group_size": selected["group_size"],
                "keep_oldest_sample_accession": selected["sample_accession"],
                "keep_oldest_run_accession": selected["run_accession"],
                "keep_oldest_first_public": selected["first_public"],
                "keep_oldest_collection_date": selected_metadata["collection_date"],
                "keep_oldest_collection_date_class": _collection_date_class(selected_metadata["collection_date"]),
                "collection_date_class": date_class,
                "collection_date_values": json.dumps(sorted(dates), ensure_ascii=False),
                "different_fields": ";".join(different) if different else "NONE",
                "different_field_values": json.dumps(
                    {field: values_by_field[field] for field in different},
                    ensure_ascii=False, sort_keys=True,
                ),
                "group_members": json.dumps(group_members, ensure_ascii=False),
            }
        )
    comparison_rows.sort(key=lambda row: row["seqkit_sum"])
    with open_tsv_writer(output) as writer:
        writer.writerow(OUTPUT_COLUMNS)
        for row in comparison_rows:
            writer.writerow([row[column] for column in OUTPUT_COLUMNS])
    return {
        "status": "PASS",
        "duplicate_group_count": len(comparison_rows),
        "collection_date_group_counts": dict(sorted(class_counts.items())),
        "different_field_group_counts": dict(sorted(field_counts.items())),
        "output": {"path": str(output.resolve()), "sha256": sha256_file(output)},
    }
