from __future__ import annotations

from pathlib import Path

from .io import iter_tsv_dicts, open_tsv_writer, read_header, sha256_file


OUTPUT_COLUMNS = ("sample_accession", "run_accession", "base_count")


class ReleaseManifestError(RuntimeError):
    """Raised when a release manifest cannot be reconciled exactly."""


def export_sample_run_base_count(
    assembly: Path, ena_latest: Path, output: Path, dataset: str
) -> dict[str, object]:
    """Export frozen release members, falling back to pipeline pass for older schemas."""
    if output.exists():
        raise ReleaseManifestError(f"refusing to overwrite output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    assembly_required = {"sample_accession", "run_accession", "dataset", "asm_pipe_filter"}
    ena_required = {"run_accession", "base_count", "atb_present_in_current_snapshot"}
    for path, required in ((assembly, assembly_required), (ena_latest, ena_required)):
        missing = required.difference(read_header(path))
        if missing:
            raise ReleaseManifestError(f"{path}: missing columns: {', '.join(sorted(missing))}")

    selected: list[tuple[str, str]] = []
    by_run: dict[str, str] = {}
    samples: set[str] = set()
    for line_number, row in iter_tsv_dicts(assembly):
        included = row["in_release_1"] == "1" if "in_release_1" in row else row["asm_pipe_filter"] == "PASS"
        if row["dataset"] != dataset or not included:
            continue
        sample, run = row["sample_accession"], row["run_accession"]
        if not run or run in {"NA", "unknown"} or "," in run or ";" in run:
            raise ReleaseManifestError(f"{assembly}:{line_number}: selected row does not have one run")
        if sample in samples or run in by_run:
            raise ReleaseManifestError(f"{assembly}:{line_number}: duplicate selected sample or run")
        selected.append((sample, run))
        samples.add(sample)
        by_run[run] = sample

    base_counts: dict[str, str] = {}
    for line_number, row in iter_tsv_dicts(ena_latest):
        run = row["run_accession"]
        if run not in by_run:
            continue
        if row["atb_present_in_current_snapshot"] != "1":
            raise ReleaseManifestError(f"{ena_latest}:{line_number}: selected run {run} is not current")
        if not row["base_count"] or row["base_count"] == "NA":
            raise ReleaseManifestError(f"{ena_latest}:{line_number}: selected run {run} has no base_count")
        base_counts[run] = row["base_count"]
    missing = [run for _, run in selected if run not in base_counts]
    if missing:
        raise ReleaseManifestError(f"{len(missing)} selected runs absent from ena_latest; first {missing[0]}")

    with open_tsv_writer(output) as writer:
        writer.writerow(OUTPUT_COLUMNS)
        for sample, run in selected:
            writer.writerow((sample, run, base_counts[run]))
    return {
        "status": "PASS", "dataset": dataset, "row_count": len(selected),
        "output": {"path": str(output.resolve()), "sha256": sha256_file(output)},
    }
