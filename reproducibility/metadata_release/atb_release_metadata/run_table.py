from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import iter_tsv_dicts, open_tsv_writer, read_header, sha256_file


PREVIOUS_COLUMNS = (
    "run_accession",
    "sample_accession",
    "in_661k",
    "ena_202505_batch",
    "fastq_md5",
    "meta_pass_atb",
    "meta_pass_661k",
    "pass",
    "comments",
)

def output_columns(snapshot_columns: list[str], current_snapshot_column: str) -> tuple[str, ...]:
    snapshots = list(snapshot_columns)
    if current_snapshot_column not in snapshots:
        snapshots.append(current_snapshot_column)
    return (
    "run_accession",
    "sample_accession",
    "in_661k",
    *snapshots,
    "ena_202505_batch",
    "fastq_md5",
    "meta_pass_atb",
    "meta_pass_661k",
    "pass",
    "failure_reasons",
    "latest_ena_source",
    "latest_ena_observed_date",
    "first_seen_date",
    "last_seen_date",
    "comments",
    )


OUTPUT_COLUMNS = output_columns(
    ["in_ena_20240625", "in_ena_20240801", "in_ena_20250506"],
    "in_ena_20260317",
)

ENA_REQUIRED_COLUMNS = (
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
    "atb_latest_source",
    "atb_latest_observed_date",
    "atb_first_seen_date",
    "atb_last_seen_date",
    "atb_present_in_current_snapshot",
    "atb_ever_sample_accession_changed",
    "atb_ever_fastq_md5_changed",
    "atb_ever_fastq_bytes_changed",
    "atb_ever_base_count_changed",
)


class RunBuildError(RuntimeError):
    """Raised when the run table cannot be derived without ambiguity."""


@dataclass(frozen=True)
class RunBuildPaths:
    database: Path
    output: Path
    report: Path


def _flag(value: str, context: str) -> int:
    if value not in {"0", "1"}:
        raise RunBuildError(f"{context}: expected 0 or 1, got {value!r}")
    return int(value)


def _samples(value: str) -> set[str]:
    return {item for item in value.replace(",", ";").split(";") if item}


def row_passes_661k(row: dict[str, str]) -> bool:
    return (
        bool(row.get("instrument_platform"))
        and row["instrument_platform"] not in {"PACBIO_SMRT", "OXFORD_NANOPORE"}
        and bool(row.get("library_source"))
        and row["library_source"] not in {"METAGENOMIC", "TRANSCRIPTOMIC"}
        and "_1.fastq.gz" in row.get("fastq_ftp", "")
        and "_2.fastq.gz" in row.get("fastq_ftp", "")
    )


def row_passes_atb(row: dict[str, str]) -> bool:
    return (
        row.get("instrument_platform") == "ILLUMINA"
        and row.get("library_strategy") == "WGS"
        and row.get("library_source") == "GENOMIC"
        and row.get("library_layout") == "PAIRED"
        and "_1.fastq.gz" in row.get("fastq_ftp", "")
        and "_2.fastq.gz" in row.get("fastq_ftp", "")
        and len(_samples(row.get("sample_accession", ""))) == 1
    )


def _new_database(path: Path) -> sqlite3.Connection:
    if path.exists():
        raise RunBuildError(f"refusing to overwrite work database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        """
        CREATE TABLE previous_run (
            run_accession TEXT PRIMARY KEY,
            sample_accession TEXT NOT NULL,
            in_661k TEXT NOT NULL,
            snapshot_flags TEXT NOT NULL,
            ena_202505_batch TEXT NOT NULL,
            fastq_md5 TEXT NOT NULL,
            meta_pass_atb TEXT NOT NULL,
            meta_pass_661k TEXT NOT NULL,
            pass TEXT NOT NULL,
            comments TEXT NOT NULL,
            seen INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    return connection


def _load_previous(connection: sqlite3.Connection, path: Path) -> tuple[int, list[str]]:
    header = read_header(path)
    missing = sorted(set(PREVIOUS_COLUMNS).difference(header))
    if missing:
        raise RunBuildError(f"{path}: missing previous run columns: {', '.join(missing)}")
    snapshot_columns = [column for column in header if column.startswith("in_ena_")]
    if not snapshot_columns:
        raise RunBuildError(f"{path}: no in_ena_* snapshot columns")
    count = 0
    connection.execute("BEGIN")
    try:
        for line_number, row in iter_tsv_dicts(path):
            run = row["run_accession"]
            if not run:
                raise RunBuildError(f"{path}:{line_number}: empty run_accession")
            for column in (
                "in_661k",
                *snapshot_columns,
                "meta_pass_atb",
                "meta_pass_661k",
                "pass",
            ):
                _flag(row[column], f"{path}:{line_number}:{column}")
            try:
                connection.execute(
                    """
                    INSERT INTO previous_run (
                        run_accession, sample_accession, in_661k,
                        snapshot_flags,
                        ena_202505_batch, fastq_md5, meta_pass_atb,
                        meta_pass_661k, pass, comments
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["run_accession"], row["sample_accession"], row["in_661k"],
                        json.dumps({column: int(row[column]) for column in snapshot_columns}, sort_keys=True),
                        row["ena_202505_batch"], row["fastq_md5"], row["meta_pass_atb"],
                        row["meta_pass_661k"], row["pass"], row["comments"],
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RunBuildError(f"{path}:{line_number}: duplicate run {run}") from error
            count += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return count, snapshot_columns


def _derive(row: dict[str, str], previous: sqlite3.Row | None,
            current_snapshot_column: str, release: str,
            snapshot_columns: list[str]) -> dict[str, Any]:
    present = _flag(
        row["atb_present_in_current_snapshot"],
        f"{row['run_accession']}:atb_present_in_current_snapshot",
    )
    current_atb = int(row_passes_atb(row)) if present else 1
    current_661k = int(row_passes_661k(row)) if present else 1

    if previous is None:
        if not present:
            raise RunBuildError(
                f"historical run {row['run_accession']} is absent from the previous run table"
            )
        in_661k = 0
        snapshot_flags: dict[str, int] = {column: 0 for column in snapshot_columns}
        batch = "NA"
        old_atb = old_661k = old_pass = 1
        comments: list[str] = []
        samples = _samples(row["sample_accession"])
        old_md5 = ""
    else:
        in_661k = int(previous["in_661k"])
        snapshot_flags = json.loads(previous["snapshot_flags"])
        batch = previous["ena_202505_batch"]
        old_atb = int(previous["meta_pass_atb"])
        old_661k = int(previous["meta_pass_661k"])
        old_pass = int(previous["pass"])
        comments = [previous["comments"]] if previous["comments"] else []
        samples = _samples(previous["sample_accession"]) | _samples(row["sample_accession"])
        old_md5 = previous["fastq_md5"]

    meta_atb = old_atb & current_atb
    meta_661k = old_661k & current_661k
    reasons: list[str] = []
    if not old_pass:
        reasons.append("PREVIOUS_RELEASE_FAIL")
    if not present:
        reasons.append("NOT_IN_CURRENT_ENA")
    if not meta_atb:
        reasons.append("META_FAIL_ATB")
    if not meta_661k:
        reasons.append("META_FAIL_661K")
    if len(samples) != 1:
        reasons.append("MULTIPLE_SAMPLE_ACCESSIONS")

    history_flags = (
        ("atb_ever_sample_accession_changed", "SAMPLE_ACCESSION_CHANGED"),
        ("atb_ever_fastq_md5_changed", "FASTQ_MD5_CHANGED"),
        ("atb_ever_fastq_bytes_changed", "FASTQ_BYTES_CHANGED"),
        ("atb_ever_base_count_changed", "BASE_COUNT_CHANGED"),
    )
    for column, reason in history_flags:
        if _flag(row[column], f"{row['run_accession']}:{column}"):
            reasons.append(reason)

    for column, reason in (
        ("fastq_ftp", "MISSING_FASTQ_FTP"),
        ("fastq_md5", "MISSING_FASTQ_MD5"),
        ("fastq_bytes", "MISSING_FASTQ_BYTES"),
        ("base_count", "MISSING_BASE_COUNT"),
        ("read_count", "MISSING_READ_COUNT"),
    ):
        if present and not row.get(column, ""):
            reasons.append(reason)

    md5 = row.get("fastq_md5", "") or old_md5 or "None"
    if old_md5 == "multiple" or "FASTQ_MD5_CHANGED" in reasons:
        md5 = "multiple"
    if previous is not None and reasons != ["PREVIOUS_RELEASE_FAIL"]:
        new_reasons = [reason for reason in reasons if reason != "PREVIOUS_RELEASE_FAIL"]
        if new_reasons:
            comments.append(f"{release}: " + ",".join(new_reasons))

    return {
        "run_accession": row["run_accession"],
        "sample_accession": ",".join(sorted(samples)),
        "in_661k": in_661k,
        **snapshot_flags,
        current_snapshot_column: present,
        "ena_202505_batch": batch,
        "fastq_md5": md5,
        "meta_pass_atb": meta_atb,
        "meta_pass_661k": meta_661k,
        "pass": int(not reasons),
        "failure_reasons": "PASS" if not reasons else ",".join(reasons),
        "latest_ena_source": row["atb_latest_source"],
        "latest_ena_observed_date": row["atb_latest_observed_date"],
        "first_seen_date": row["atb_first_seen_date"],
        "last_seen_date": row["atb_last_seen_date"],
        "comments": ";".join(comments),
    }


def build_run_table(
    previous_path: Path,
    ena_latest_path: Path,
    paths: RunBuildPaths,
    current_snapshot_column: str = "in_ena_20260317",
    release: str = "202603",
) -> dict[str, Any]:
    for output in (paths.output, paths.report):
        if output.exists():
            raise RunBuildError(f"refusing to overwrite output: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
    ena_header = read_header(ena_latest_path)
    missing = sorted(set(ENA_REQUIRED_COLUMNS).difference(ena_header))
    if missing:
        raise RunBuildError(f"{ena_latest_path}: missing columns: {', '.join(missing)}")

    connection = _new_database(paths.database)
    connection.row_factory = sqlite3.Row
    try:
        previous_count, snapshot_columns = _load_previous(connection, previous_path)
        columns = output_columns(snapshot_columns, current_snapshot_column)
        reason_counts: Counter[str] = Counter()
        pass_count = 0
        old_became_fail = 0
        new_count = 0
        output_count = 0
        with open_tsv_writer(paths.output) as writer:
            writer.writerow(columns)
            for _, row in iter_tsv_dicts(ena_latest_path):
                run = row["run_accession"]
                previous = connection.execute(
                    "SELECT * FROM previous_run WHERE run_accession = ?", (run,)
                ).fetchone()
                result = _derive(row, previous, current_snapshot_column, release, snapshot_columns)
                writer.writerow([result[column] for column in columns])
                reasons = result["failure_reasons"].split(",")
                reason_counts.update(reasons)
                pass_count += result["pass"]
                new_count += int(previous is None)
                old_became_fail += int(
                    previous is not None and previous["pass"] == "1" and result["pass"] == 0
                )
                if previous is not None:
                    connection.execute(
                        "UPDATE previous_run SET seen = 1 WHERE run_accession = ?", (run,)
                    )
                output_count += 1
        connection.commit()
        unseen = connection.execute(
            "SELECT run_accession FROM previous_run WHERE seen = 0 ORDER BY run_accession LIMIT 1"
        ).fetchone()
        if unseen is not None:
            count = connection.execute(
                "SELECT count(*) FROM previous_run WHERE seen = 0"
            ).fetchone()[0]
            raise RunBuildError(
                f"{count:,} previous runs are absent from ena_latest; first is {unseen[0]}"
            )
    finally:
        connection.close()

    report: dict[str, Any] = {
        "status": "PASS",
        "previous_run_count": previous_count,
        "new_run_count": new_count,
        "output_run_count": output_count,
        "pass_count": pass_count,
        "fail_count": output_count - pass_count,
        "previously_passing_runs_now_failing": old_became_fail,
        "failure_reason_counts": dict(sorted(reason_counts.items())),
        "inputs": {
            "previous_run": {"path": str(previous_path.resolve()), "sha256": sha256_file(previous_path)},
            "ena_latest": {"path": str(ena_latest_path.resolve()), "sha256": sha256_file(ena_latest_path)},
        },
        "output": {
            "path": str(paths.output.resolve()),
            "sha256": sha256_file(paths.output),
            "columns": list(columns),
        },
    }
    paths.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
