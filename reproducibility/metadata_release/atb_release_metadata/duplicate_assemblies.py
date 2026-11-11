from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .io import iter_tsv_dicts, open_tsv_writer, read_header, sha256_file


EMPTY_SEQKIT_SUM = "seqkit.v0.1_LS_k0_56fc8fa222734a7d4b9ddedcca604bbf"
OUTPUT_COLUMNS = (
    "seqkit_sum", "group_size", "sample_accession", "run_accession",
    "duplicate_status", "selection_basis", "first_public", "first_created",
    "run_date", "fastq_ftp", "fastq_md5", "fastq_bytes", "base_count",
    "read_count", "assembly_dataset", "asm_pipe_filter", "hq_filter",
)


class DuplicateAssemblyError(RuntimeError):
    """Raised when duplicate-assembly reporting would be ambiguous."""


def _natural_accession(value: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"([A-Za-z]+)([0-9]+)", value)
    if match is None:
        return value, -1, value
    return match.group(1), int(match.group(2)), value


def _date_key(value: str) -> tuple[int, str]:
    # Missing dates sort after known dates when selecting the stable oldest row.
    return (0, value) if value and value != "NA" else (1, "")


def _oldest_key(row: dict[str, str]) -> tuple[tuple[int, str], tuple[str, int, str]]:
    return (
        _date_key(row["first_public"]),
        _natural_accession(row["sample_accession"]),
    )


def export_duplicate_assembly_report(
    assembly: Path, ena_latest: Path, output: Path, dataset: str
) -> dict[str, object]:
    if output.exists():
        raise DuplicateAssemblyError(f"refusing to overwrite output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    assembly_required = {
        "sample_accession", "run_accession", "assembly_seqkit_sum", "dataset",
        "asm_pipe_filter", "hq_filter",
    }
    ena_required = {
        "run_accession", "sample_accession", "first_public", "first_created",
        "run_date", "fastq_ftp", "fastq_md5", "fastq_bytes", "base_count",
        "read_count", "atb_present_in_current_snapshot",
    }
    for path, required in ((assembly, assembly_required), (ena_latest, ena_required)):
        missing = required.difference(read_header(path))
        if missing:
            raise DuplicateAssemblyError(f"{path}: missing columns: {', '.join(sorted(missing))}")

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    run_to_sample: dict[str, str] = {}
    for line_number, row in iter_tsv_dicts(assembly):
        if row["dataset"] != dataset or row["assembly_seqkit_sum"] in {"NA", EMPTY_SEQKIT_SUM}:
            continue
        run = row["run_accession"]
        if not run or run in {"NA", "unknown"} or "," in run:
            continue
        groups[row["assembly_seqkit_sum"]].append(row)
        if run in run_to_sample and run_to_sample[run] != row["sample_accession"]:
            raise DuplicateAssemblyError(
                f"{assembly}:{line_number}: run {run} maps to multiple assembly samples"
            )
        run_to_sample[run] = row["sample_accession"]
    groups = {key: rows for key, rows in groups.items() if len(rows) > 1}
    if not groups:
        raise DuplicateAssemblyError(f"{assembly}: no non-empty duplicate seqkit sums in {dataset}")

    ena: dict[str, dict[str, str]] = {}
    wanted = set(run_to_sample)
    for line_number, row in iter_tsv_dicts(ena_latest):
        run = row["run_accession"]
        if run not in wanted:
            continue
        if row["atb_present_in_current_snapshot"] != "1":
            raise DuplicateAssemblyError(
                f"{ena_latest}:{line_number}: duplicate-report run {run} is not current"
            )
        if row["sample_accession"] != run_to_sample[run]:
            raise DuplicateAssemblyError(
                f"{ena_latest}:{line_number}: run/sample differs from assembly: {run}"
            )
        ena[run] = row
    missing_runs = sorted(wanted - ena.keys())
    if missing_runs:
        raise DuplicateAssemblyError(
            f"{len(missing_runs)} duplicate-report runs absent from current ENA; first {missing_runs[0]}"
        )

    rows_out: list[dict[str, str]] = []
    for seqkit_sum, assembly_rows in groups.items():
        joined = []
        for assembly_row in assembly_rows:
            run = assembly_row["run_accession"]
            joined.append({**assembly_row, **ena[run]})
        chosen = min(joined, key=_oldest_key)
        for row in joined:
            dates = "earliest_first_public>natural_sample_accession"
            rows_out.append(
                {
                    "seqkit_sum": seqkit_sum,
                    "group_size": str(len(joined)),
                    "sample_accession": row["sample_accession"],
                    "run_accession": row["run_accession"],
                    "duplicate_status": "KEEP_OLDEST" if row["run_accession"] == chosen["run_accession"] else "LIKELY_DUPLICATE",
                    "selection_basis": dates,
                    "first_public": row["first_public"],
                    "first_created": row["first_created"],
                    "run_date": row["run_date"],
                    "fastq_ftp": row["fastq_ftp"],
                    "fastq_md5": row["fastq_md5"],
                    "fastq_bytes": row["fastq_bytes"],
                    "base_count": row["base_count"],
                    "read_count": row["read_count"],
                    "assembly_dataset": row["dataset"],
                    "asm_pipe_filter": row["asm_pipe_filter"],
                    "hq_filter": row["hq_filter"],
                }
            )
    rows_out.sort(key=lambda row: (row["seqkit_sum"], row["duplicate_status"], row["run_accession"]))
    with open_tsv_writer(output) as writer:
        writer.writerow(OUTPUT_COLUMNS)
        for row in rows_out:
            writer.writerow([row[column] for column in OUTPUT_COLUMNS])
    return {
        "status": "PASS",
        "dataset": dataset,
        "duplicate_group_count": len(groups),
        "row_count": len(rows_out),
        "likely_duplicate_count": sum(row["duplicate_status"] == "LIKELY_DUPLICATE" for row in rows_out),
        "output": {"path": str(output.resolve()), "sha256": sha256_file(output)},
    }
