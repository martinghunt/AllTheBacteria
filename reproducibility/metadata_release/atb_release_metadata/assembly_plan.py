from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import iter_tsv_dicts, open_tsv_writer, read_header, sha256_file


class AssemblyPlanError(RuntimeError):
    """Raised when an assembly candidate set is ambiguous."""


@dataclass(frozen=True)
class AssemblyPlanPaths:
    database: Path
    output: Path
    report: Path


def _new_database(path: Path) -> sqlite3.Connection:
    if path.exists():
        raise AssemblyPlanError(f"refusing to overwrite work database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE old_sample (sample TEXT PRIMARY KEY);
        CREATE TABLE pass_run (
            sample TEXT NOT NULL,
            run TEXT NOT NULL,
            PRIMARY KEY (sample, run)
        ) WITHOUT ROWID;
        """
    )
    return connection


def _split_samples(value: str) -> list[str]:
    return sorted({x for x in value.replace(",", ";").split(";") if x})


def make_assembly_plan(
    previous_assembly: Path,
    run_table: Path,
    paths: AssemblyPlanPaths,
    executed: Path | None = None,
) -> dict[str, Any]:
    for output in (paths.output, paths.report):
        if output.exists():
            raise AssemblyPlanError(f"refusing to overwrite output: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
    for path, required in (
        (previous_assembly, {"sample_accession"}),
        (run_table, {"run_accession", "sample_accession", "pass"}),
    ):
        missing = required.difference(read_header(path))
        if missing:
            raise AssemblyPlanError(f"{path}: missing columns: {', '.join(sorted(missing))}")

    connection = _new_database(paths.database)
    try:
        connection.execute("BEGIN")
        for line_number, row in iter_tsv_dicts(previous_assembly):
            try:
                connection.execute(
                    "INSERT INTO old_sample VALUES (?)", (row["sample_accession"],)
                )
            except sqlite3.IntegrityError as error:
                raise AssemblyPlanError(
                    f"{previous_assembly}:{line_number}: duplicate sample "
                    f"{row['sample_accession']}"
                ) from error
        connection.commit()

        connection.execute("BEGIN")
        for line_number, row in iter_tsv_dicts(run_table):
            if row["pass"] not in {"0", "1"}:
                raise AssemblyPlanError(f"{run_table}:{line_number}: invalid pass flag")
            if row["pass"] == "0":
                continue
            samples = _split_samples(row["sample_accession"])
            if len(samples) != 1:
                raise AssemblyPlanError(
                    f"{run_table}:{line_number}: passing run does not map to one sample"
                )
            connection.execute(
                "INSERT INTO pass_run VALUES (?, ?)",
                (samples[0], row["run_accession"]),
            )
        connection.commit()

        multiplicities = Counter()
        candidates: dict[str, str] = {}
        query = """
            SELECT p.sample, min(p.run), count(*)
            FROM pass_run p LEFT JOIN old_sample o ON p.sample=o.sample
            WHERE o.sample IS NULL GROUP BY p.sample ORDER BY p.sample
        """
        with open_tsv_writer(paths.output) as writer:
            writer.writerow(("sample_accession", "run_accession"))
            for sample, run, count in connection.execute(query):
                multiplicities[count] += 1
                if count == 1:
                    candidates[sample] = run
                    writer.writerow((sample, run))
    finally:
        connection.close()

    comparison: dict[str, Any] | None = None
    if executed is not None:
        actual: dict[str, str] = {}
        with executed.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                fields = line.split()
                if len(fields) != 2:
                    raise AssemblyPlanError(
                        f"{executed}:{line_number}: expected sample and run"
                    )
                sample, run = fields
                if sample in actual:
                    raise AssemblyPlanError(f"{executed}:{line_number}: duplicate {sample}")
                actual[sample] = run
        overlap = candidates.keys() & actual.keys()
        different = sorted(s for s in overlap if candidates[s] != actual[s])
        comparison = {
            "executed_count": len(actual),
            "intersection_count": len(overlap),
            "candidate_only_count": len(candidates.keys() - actual.keys()),
            "executed_only_count": len(actual.keys() - candidates.keys()),
            "different_run_count": len(different),
            "candidate_only_samples": sorted(candidates.keys() - actual.keys()),
            "executed_only_samples": sorted(actual.keys() - candidates.keys()),
            "different_runs": [
                {"sample_accession": s, "candidate": candidates[s], "executed": actual[s]}
                for s in different
            ],
        }

    report: dict[str, Any] = {
        "status": "PASS",
        "candidate_count": len(candidates),
        "new_sample_passing_run_multiplicity": {
            str(k): v for k, v in sorted(multiplicities.items())
        },
        "comparison_to_executed": comparison,
        "inputs": {
            "previous_assembly": {"path": str(previous_assembly.resolve()), "sha256": sha256_file(previous_assembly)},
            "run_table": {"path": str(run_table.resolve()), "sha256": sha256_file(run_table)},
            **({"executed": {"path": str(executed.resolve()), "sha256": sha256_file(executed)}} if executed else {}),
        },
        "output": {"path": str(paths.output.resolve()), "sha256": sha256_file(paths.output)},
    }
    paths.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report
