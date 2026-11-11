from __future__ import annotations

import json
from pathlib import Path

from .io import iter_tsv_dicts, open_tsv_writer, read_header, sha256_file


OUTPUT_COLUMNS = (
    "sample_accession",
    "run_accession",
    "dataset",
    "assembly_accession",
    "asm_pipe_filter",
    "hq_filter",
    "comments",
)


class HistoricalGapError(RuntimeError):
    """Raised when the historical assembly-provenance gap cannot be reconciled."""


def export_historical_assembly_run_gaps(
    assembly: Path, assembly_report: Path, output: Path
) -> dict[str, object]:
    if output.exists():
        raise HistoricalGapError(f"refusing to overwrite output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    required = {
        "sample_accession", "run_accession", "dataset", "assembly_accession",
        "asm_pipe_filter", "hq_filter", "comments",
    }
    missing = required.difference(read_header(assembly))
    if missing:
        raise HistoricalGapError(f"{assembly}: missing columns: {', '.join(sorted(missing))}")
    report = json.loads(assembly_report.read_text(encoding="utf-8"))
    try:
        missing_runs = set(report["historical_assembly_runs_absent_from_run_table"])
    except KeyError as error:
        raise HistoricalGapError(
            f"{assembly_report}: missing historical assembly-run gap list"
        ) from error
    rows: list[dict[str, str]] = []
    seen_runs: set[str] = set()
    for _, row in iter_tsv_dicts(assembly):
        for run in row["run_accession"].split(","):
            if run not in missing_runs:
                continue
            if run in seen_runs:
                raise HistoricalGapError(f"{assembly}: run {run} maps to multiple samples")
            seen_runs.add(run)
            rows.append(
                {
                    "sample_accession": row["sample_accession"],
                    "run_accession": run,
                    "dataset": row["dataset"],
                    "assembly_accession": row["assembly_accession"],
                    "asm_pipe_filter": row["asm_pipe_filter"],
                    "hq_filter": row["hq_filter"],
                    "comments": row["comments"],
                }
            )
    if seen_runs != missing_runs:
        unresolved = sorted(missing_runs - seen_runs)
        raise HistoricalGapError(
            f"{len(unresolved)} report runs not found in assembly table; first {unresolved[0]}"
        )
    rows.sort(key=lambda row: row["run_accession"])
    with open_tsv_writer(output) as writer:
        writer.writerow(OUTPUT_COLUMNS)
        for row in rows:
            writer.writerow([row[column] for column in OUTPUT_COLUMNS])
    return {
        "status": "PASS",
        "row_count": len(rows),
        "output": {"path": str(output.resolve()), "sha256": sha256_file(output)},
    }
