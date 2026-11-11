from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .config import BuildConfig, Source
from .io import iter_tsv_dicts, open_tsv_writer, read_header, sha256_file


ATB_COLUMNS = (
    "atb_latest_source",
    "atb_latest_observed_date",
    "atb_first_seen_date",
    "atb_last_seen_date",
    "atb_observation_count",
    "atb_present_in_current_snapshot",
    "atb_current_snapshot",
    "atb_ever_sample_accession_changed",
    "atb_ever_fastq_md5_changed",
    "atb_ever_fastq_bytes_changed",
    "atb_ever_base_count_changed",
)

TRACKED_FIELDS = (
    "sample_accession",
    "fastq_md5",
    "fastq_bytes",
    "base_count",
)

CURRENT_REQUIRED_VALUE_FIELDS = (
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
)

CHANGE_COLUMNS = (
    "run_accession",
    "change_type",
    "field",
    "old_value",
    "new_value",
    "old_source",
    "old_observed_date",
    "new_source",
    "new_observed_date",
)


class BuildError(RuntimeError):
    """Raised when release metadata invariants are violated."""


@dataclass(frozen=True)
class BuildPaths:
    database: Path
    output: Path
    changes: Path
    manifest: Path


def _canonical_value(field: str, row: dict[str, str], available: set[str]) -> str | None:
    if field not in available:
        return None
    value = row.get(field, "")
    if not value:
        return None
    if field == "sample_accession":
        return ";".join(sorted(x for x in value.replace(",", ";").split(";") if x))
    if field in {"fastq_md5", "fastq_bytes"}:
        values = value.split(";")
        paths = row.get("fastq_ftp", "").split(";")
        if "fastq_ftp" in available and all(paths) and len(paths) == len(values):
            pairs = sorted(
                (path.rsplit("/", 1)[-1], item)
                for path, item in zip(paths, values, strict=True)
            )
            return json.dumps(pairs, separators=(",", ":"))
        return json.dumps(values, separators=(",", ":"))
    return value


def _new_database(path: Path) -> sqlite3.Connection:
    if path.exists():
        raise BuildError(f"refusing to overwrite work database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE state (
            run_accession TEXT PRIMARY KEY,
            latest_precedence INTEGER NOT NULL,
            latest_date TEXT NOT NULL,
            latest_source TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            observation_count INTEGER NOT NULL,
            present_current INTEGER NOT NULL,
            previous_present INTEGER,
            latest_sample_accession TEXT,
            latest_fastq_md5 TEXT,
            latest_fastq_bytes TEXT,
            latest_base_count TEXT,
            latest_fastq_ftp TEXT,
            baseline_sample_accession TEXT,
            baseline_fastq_md5 TEXT,
            baseline_fastq_bytes TEXT,
            baseline_base_count TEXT,
            ever_sample_accession_changed INTEGER NOT NULL,
            ever_fastq_md5_changed INTEGER NOT NULL,
            ever_fastq_bytes_changed INTEGER NOT NULL,
            ever_base_count_changed INTEGER NOT NULL
        );
        CREATE INDEX state_latest_source ON state(latest_source);
        CREATE TEMP TABLE source_runs (
            run_accession TEXT NOT NULL,
            observed_date TEXT NOT NULL,
            PRIMARY KEY (run_accession, observed_date)
        ) WITHOUT ROWID;
        CREATE TABLE changes (
            run_accession TEXT NOT NULL,
            change_type TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT NOT NULL,
            new_value TEXT NOT NULL,
            old_source TEXT NOT NULL,
            old_date TEXT NOT NULL,
            new_source TEXT NOT NULL,
            new_date TEXT NOT NULL
        );
        """
    )
    return connection


def _ensure_outputs_absent(paths: BuildPaths) -> None:
    for path in (paths.output, paths.changes, paths.manifest):
        if path.exists():
            raise BuildError(f"refusing to overwrite output: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)


def _source_schema(source: Source) -> list[str]:
    header = read_header(source.path)
    if "run_accession" not in header:
        raise BuildError(f"{source.path}: missing run_accession column")
    if source.date_column is not None and source.date_column not in header:
        raise BuildError(f"{source.path}: missing date column {source.date_column!r}")
    collisions = sorted(set(header).intersection(ATB_COLUMNS))
    if collisions:
        raise BuildError(
            f"{source.path}: input uses reserved ATB column(s): {', '.join(collisions)}"
        )
    return header


def _ena_columns(header: list[str], excluded: tuple[str, ...] = ()) -> list[str]:
    excluded_set = set(excluded).union(ATB_COLUMNS)
    return [column for column in header if column not in excluded_set]


def _raw_tracked(row: dict[str, str]) -> dict[str, str | None]:
    return {
        "sample_accession": row.get("sample_accession") or None,
        "fastq_md5": row.get("fastq_md5") or None,
        "fastq_bytes": row.get("fastq_bytes") or None,
        "base_count": row.get("base_count") or None,
        "fastq_ftp": row.get("fastq_ftp") or None,
    }


def _state_from_previous(connection: sqlite3.Connection, path: Path) -> list[str]:
    header = read_header(path)
    if "run_accession" not in header:
        raise BuildError(f"{path}: previous ena_latest is missing run_accession")
    missing = sorted(set(ATB_COLUMNS).difference(header))
    if missing:
        raise BuildError(f"{path}: previous ena_latest is missing: {', '.join(missing)}")
    ena_columns = _ena_columns(header)
    available = set(ena_columns)
    connection.execute("BEGIN")
    count = 0
    try:
        for line_number, row in iter_tsv_dicts(path):
            run = row["run_accession"]
            if not run:
                raise BuildError(f"{path}:{line_number}: empty run_accession")
            canonical = {
                field: _canonical_value(field, row, available) for field in TRACKED_FIELDS
            }
            raw = _raw_tracked(row)
            try:
                connection.execute(
                    """
                    INSERT INTO state VALUES (
                        ?, 0, ?, ?, ?, ?, ?, 0, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?
                    )
                    """,
                    (
                        run,
                        row["atb_latest_observed_date"],
                        row["atb_latest_source"],
                        row["atb_first_seen_date"],
                        row["atb_last_seen_date"],
                        int(row["atb_observation_count"]),
                        int(row["atb_present_in_current_snapshot"]),
                        raw["sample_accession"],
                        raw["fastq_md5"],
                        raw["fastq_bytes"],
                        raw["base_count"],
                        raw["fastq_ftp"],
                        canonical["sample_accession"],
                        canonical["fastq_md5"],
                        canonical["fastq_bytes"],
                        canonical["base_count"],
                        int(row["atb_ever_sample_accession_changed"]),
                        int(row["atb_ever_fastq_md5_changed"]),
                        int(row["atb_ever_fastq_bytes_changed"]),
                        int(row["atb_ever_base_count_changed"]),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise BuildError(f"{path}:{line_number}: duplicate run_accession {run}") from error
            count += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    print(f"Loaded {count:,} runs from previous ena_latest", file=sys.stderr, flush=True)
    return ena_columns


def _add_change(
    connection: sqlite3.Connection,
    run: str,
    change_type: str,
    field: str,
    old_value: str,
    new_value: str,
    old_source: str,
    old_date: str,
    source: Source,
    observed_date: str,
) -> None:
    connection.execute(
        "INSERT INTO changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run,
            change_type,
            field,
            old_value,
            new_value,
            old_source,
            old_date,
            source.name,
            observed_date,
        ),
    )


def _old_tracked(previous: sqlite3.Row) -> dict[str, str]:
    return {
        "sample_accession": previous["latest_sample_accession"] or "",
        "fastq_md5": previous["latest_fastq_md5"] or "",
        "fastq_bytes": previous["latest_fastq_bytes"] or "",
        "base_count": previous["latest_base_count"] or "",
        "fastq_ftp": previous["latest_fastq_ftp"] or "",
    }


def _record_current_changes(
    connection: sqlite3.Connection,
    run: str,
    source: Source,
    observed_date: str,
    row: dict[str, str],
    previous: sqlite3.Row | None,
    source_columns: set[str],
) -> None:
    if previous is None:
        _add_change(
            connection, run, "NEW_RUN", "", "", "", "", "", source, observed_date
        )
    elif previous["previous_present"] == 0:
        _add_change(
            connection,
            run,
            "RUN_REAPPEARED",
            "",
            "",
            "",
            previous["latest_source"],
            previous["latest_date"],
            source,
            observed_date,
        )
    old_row = _old_tracked(previous) if previous is not None else {}
    old_columns = {field for field, value in old_row.items() if value != ""}
    old_source = previous["latest_source"] if previous is not None else ""
    old_date = previous["latest_date"] if previous is not None else ""
    for field in CURRENT_REQUIRED_VALUE_FIELDS:
        if field in source_columns and not row.get(field, ""):
            _add_change(
                connection,
                run,
                "CURRENT_VALUE_MISSING",
                field,
                old_row.get(field, ""),
                "",
                old_source,
                old_date,
                source,
                observed_date,
            )
    change_names = {
        "sample_accession": "SAMPLE_ACCESSION_CHANGED",
        "fastq_md5": "FASTQ_MD5_CHANGED",
        "fastq_bytes": "FASTQ_BYTES_CHANGED",
        "base_count": "BASE_COUNT_CHANGED",
    }
    for field in TRACKED_FIELDS:
        if field not in source_columns:
            continue
        old = old_row.get(field, "")
        new = row.get(field, "")
        if new and old and _canonical_value(field, old_row, old_columns) != _canonical_value(
            field, row, source_columns
        ):
            _add_change(
                connection,
                run,
                change_names[field],
                field,
                old,
                new,
                old_source,
                old_date,
                source,
                observed_date,
            )


def _process_source(
    connection: sqlite3.Connection,
    source: Source,
    header: list[str],
    current_source: str,
) -> dict[str, int]:
    source_columns = set(header)
    is_current = source.name == current_source
    connection.execute("DELETE FROM source_runs")
    connection.commit()
    connection.execute("BEGIN")
    count = 0
    try:
        for line_number, row in iter_tsv_dicts(source.path):
            run = row["run_accession"]
            if not run:
                raise BuildError(f"{source.path}:{line_number}: empty run_accession")
            observed_date = source.date_for_row(row, line_number)
            try:
                connection.execute(
                    "INSERT INTO source_runs VALUES (?, ?)", (run, observed_date)
                )
            except sqlite3.IntegrityError as error:
                raise BuildError(
                    f"{source.path}:{line_number}: duplicate observation for "
                    f"run_accession {run} on {observed_date or '<unknown date>'}"
                ) from error

            previous = connection.execute(
                "SELECT * FROM state WHERE run_accession = ?", (run,)
            ).fetchone()
            if is_current:
                _record_current_changes(
                    connection,
                    run,
                    source,
                    observed_date,
                    row,
                    previous,
                    source_columns,
                )

            canonical = {
                field: _canonical_value(field, row, source_columns)
                for field in TRACKED_FIELDS
            }
            raw = _raw_tracked(row)
            if previous is None:
                connection.execute(
                    """
                    INSERT INTO state VALUES (
                        ?, ?, ?, ?, ?, ?, 1, ?, NULL,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        0, 0, 0, 0
                    )
                    """,
                    (
                        run,
                        source.precedence,
                        observed_date,
                        source.name,
                        observed_date,
                        observed_date,
                        int(is_current),
                        raw["sample_accession"],
                        raw["fastq_md5"],
                        raw["fastq_bytes"],
                        raw["base_count"],
                        raw["fastq_ftp"],
                        canonical["sample_accession"],
                        canonical["fastq_md5"],
                        canonical["fastq_bytes"],
                        canonical["base_count"],
                    ),
                )
            else:
                updates: dict[str, Any] = {
                    "observation_count": previous["observation_count"] + 1,
                    "present_current": int(is_current) or previous["present_current"],
                }
                for field in TRACKED_FIELDS:
                    baseline_column = f"baseline_{field}"
                    ever_column = f"ever_{field}_changed"
                    baseline = previous[baseline_column]
                    value = canonical[field]
                    updates[baseline_column] = baseline if baseline is not None else value
                    updates[ever_column] = int(
                        bool(previous[ever_column])
                        or (baseline is not None and value is not None and baseline != value)
                    )
                incoming_key = (source.precedence, observed_date)
                latest_key = (previous["latest_precedence"], previous["latest_date"])
                if incoming_key > latest_key:
                    updates.update(
                        latest_precedence=source.precedence,
                        latest_date=observed_date,
                        latest_source=source.name,
                        last_seen=observed_date,
                        latest_sample_accession=raw["sample_accession"],
                        latest_fastq_md5=raw["fastq_md5"],
                        latest_fastq_bytes=raw["fastq_bytes"],
                        latest_base_count=raw["base_count"],
                        latest_fastq_ftp=raw["fastq_ftp"],
                    )
                assignments = ", ".join(f"{column} = ?" for column in updates)
                connection.execute(
                    f"UPDATE state SET {assignments} WHERE run_accession = ?",
                    (*updates.values(), run),
                )
            count += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    print(f"Loaded {count:,} rows from {source.name}", file=sys.stderr, flush=True)
    unique_runs = connection.execute(
        "SELECT count(DISTINCT run_accession) FROM source_runs"
    ).fetchone()[0]
    return {"row_count": count, "unique_run_count": unique_runs}


def _record_removed_runs(connection: sqlite3.Connection, current: Source) -> int:
    rows = connection.execute(
        """
        SELECT run_accession, latest_source, latest_date
        FROM state
        WHERE present_current = 0
          AND (previous_present IS NULL OR previous_present = 1)
        ORDER BY run_accession
        """
    )
    count = 0
    connection.execute("BEGIN")
    try:
        for run, old_source, old_date in rows:
            _add_change(
                connection,
                run,
                "RUN_REMOVED",
                "",
                "",
                "",
                old_source,
                old_date,
                current,
                current.date or "",
            )
            count += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return count


def _output_columns(
    current_header: list[str],
    previous_columns: list[str],
    sources: tuple[Source, ...],
) -> list[str]:
    current = _ena_columns(current_header, sources[-1].exclude_columns)
    all_columns = set(previous_columns)
    for source in sources:
        all_columns.update(_ena_columns(read_header(source.path), source.exclude_columns))
    all_columns.discard("run_accession")
    current_tail = [column for column in current if column != "run_accession"]
    historical_only = sorted(all_columns.difference(current_tail))
    return ["run_accession", *current_tail, *historical_only, *ATB_COLUMNS]


def _atb_output(state: sqlite3.Row, current: Source) -> dict[str, str | int]:
    return {
        "atb_latest_source": state["latest_source"],
        "atb_latest_observed_date": state["latest_date"],
        "atb_first_seen_date": state["first_seen"],
        "atb_last_seen_date": state["last_seen"],
        "atb_observation_count": state["observation_count"],
        "atb_present_in_current_snapshot": state["present_current"],
        "atb_current_snapshot": current.name,
        "atb_ever_sample_accession_changed": state[
            "ever_sample_accession_changed"
        ],
        "atb_ever_fastq_md5_changed": state["ever_fastq_md5_changed"],
        "atb_ever_fastq_bytes_changed": state["ever_fastq_bytes_changed"],
        "atb_ever_base_count_changed": state["ever_base_count_changed"],
    }


def _write_selected_source(
    connection: sqlite3.Connection,
    writer: Any,
    source: Source,
    columns: list[str],
    current: Source,
) -> int:
    wanted = {
        row[0]
        for row in connection.execute(
            "SELECT run_accession FROM state WHERE latest_source = ?", (source.name,)
        )
    }
    if not wanted:
        return 0
    source_columns = _ena_columns(read_header(source.path), source.exclude_columns)
    count = 0
    for _, raw in iter_tsv_dicts(source.path):
        run = raw["run_accession"]
        if run not in wanted:
            continue
        state = connection.execute(
            "SELECT * FROM state WHERE run_accession = ?", (run,)
        ).fetchone()
        if source.date_for_row(raw, 0) != state["latest_date"]:
            continue
        output: dict[str, Any] = {column: raw.get(column, "") for column in source_columns}
        output.update(_atb_output(state, current))
        writer.writerow([output.get(column, "") for column in columns])
        wanted.remove(run)
        count += 1
    if wanted:
        raise BuildError(
            f"could not recover {len(wanted):,} selected rows from {source.name}; "
            f"first missing run is {min(wanted)}"
        )
    return count


def _write_selected_previous(
    connection: sqlite3.Connection,
    writer: Any,
    path: Path,
    new_source_names: set[str],
    columns: list[str],
    current: Source,
) -> int:
    placeholders = ",".join("?" for _ in new_source_names)
    query = f"SELECT run_accession FROM state WHERE latest_source NOT IN ({placeholders})"
    wanted = {row[0] for row in connection.execute(query, tuple(sorted(new_source_names)))}
    if not wanted:
        return 0
    ena_columns = _ena_columns(read_header(path))
    count = 0
    for _, raw in iter_tsv_dicts(path):
        run = raw["run_accession"]
        if run not in wanted:
            continue
        state = connection.execute(
            "SELECT * FROM state WHERE run_accession = ?", (run,)
        ).fetchone()
        output: dict[str, Any] = {column: raw.get(column, "") for column in ena_columns}
        output.update(_atb_output(state, current))
        writer.writerow([output.get(column, "") for column in columns])
        wanted.remove(run)
        count += 1
    if wanted:
        raise BuildError(
            f"could not recover {len(wanted):,} selected rows from previous ena_latest; "
            f"first missing run is {min(wanted)}"
        )
    return count


def _write_latest(
    connection: sqlite3.Connection,
    path: Path,
    columns: list[str],
    config: BuildConfig,
) -> int:
    count = 0
    with open_tsv_writer(path) as writer:
        writer.writerow(columns)
        for source in reversed(config.sources):
            written = _write_selected_source(
                connection, writer, source, columns, config.current
            )
            print(
                f"Wrote {written:,} latest rows from {source.name}",
                file=sys.stderr,
                flush=True,
            )
            count += written
        if config.previous_latest is not None:
            written = _write_selected_previous(
                connection,
                writer,
                config.previous_latest,
                {source.name for source in config.sources},
                columns,
                config.current,
            )
            print(
                f"Wrote {written:,} retained rows from previous ena_latest",
                file=sys.stderr,
                flush=True,
            )
            count += written
    expected = connection.execute("SELECT count(*) FROM state").fetchone()[0]
    if count != expected:
        raise BuildError(f"wrote {count:,} latest rows, expected {expected:,}")
    return count


def _write_changes(connection: sqlite3.Connection, path: Path) -> int:
    count = 0
    with open_tsv_writer(path) as writer:
        writer.writerow(CHANGE_COLUMNS)
        for row in connection.execute(
            """
            SELECT run_accession, change_type, field, old_value, new_value,
                   old_source, old_date, new_source, new_date
            FROM changes
            ORDER BY run_accession, change_type, field
            """
        ):
            writer.writerow(row)
            count += 1
    return count


def build(config: BuildConfig, paths: BuildPaths) -> dict[str, Any]:
    _ensure_outputs_absent(paths)
    source_headers = {source.name: _source_schema(source) for source in config.sources}
    current_header = source_headers[config.current_source]
    missing_current = sorted(set(config.required_current_columns).difference(current_header))
    if missing_current:
        raise BuildError(
            f"current source is missing required column(s): {', '.join(missing_current)}"
        )

    connection = _new_database(paths.database)
    connection.row_factory = sqlite3.Row
    try:
        previous_columns: list[str] = []
        if config.previous_latest is not None:
            previous_columns = _state_from_previous(connection, config.previous_latest)

        source_stats: dict[str, dict[str, int]] = {}
        for source in config.sources:
            source_stats[source.name] = _process_source(
                connection,
                source,
                source_headers[source.name],
                config.current_source,
            )

        wrong_latest = connection.execute(
            """
            SELECT run_accession, latest_source
            FROM state
            WHERE present_current = 1 AND latest_source != ?
            ORDER BY run_accession
            LIMIT 1
            """,
            (config.current_source,),
        ).fetchone()
        if wrong_latest is not None:
            raise BuildError(
                f"current record did not win precedence for {wrong_latest['run_accession']}; "
                f"selected {wrong_latest['latest_source']}"
            )

        removed_count = _record_removed_runs(connection, config.current)
        columns = _output_columns(current_header, previous_columns, config.sources)
        latest_count = _write_latest(connection, paths.output, columns, config)
        change_count = _write_changes(connection, paths.changes)
    finally:
        connection.close()

    inputs: list[dict[str, Any]] = []
    if config.previous_latest is not None:
        inputs.append(
            {
                "role": "previous_latest",
                "path": str(config.previous_latest),
                "sha256": sha256_file(config.previous_latest),
                "size": config.previous_latest.stat().st_size,
            }
        )
    schema_union = sorted({column for header in source_headers.values() for column in header})
    previous_schema: set[str] = set()
    for source in config.sources:
        header = source_headers[source.name]
        header_set = set(header)
        inputs.append(
            {
                "role": "ena_source",
                "name": source.name,
                "kind": source.kind,
                "path": str(source.path),
                "sha256": sha256_file(source.path),
                "size": source.path.stat().st_size,
                "date": source.date,
                "date_column": source.date_column,
                "precedence": source.precedence,
                "columns": header,
                "columns_absent_from_union": sorted(set(schema_union).difference(header_set)),
                "columns_added_since_previous_source": sorted(header_set.difference(previous_schema)),
                "columns_removed_since_previous_source": sorted(previous_schema.difference(header_set)),
                "excluded_columns": list(source.exclude_columns),
                **source_stats[source.name],
            }
        )
        previous_schema = header_set

    manifest: dict[str, Any] = {
        "format_version": 1,
        "tool_version": __version__,
        "release": config.release,
        "current_source": config.current_source,
        "ena_source_schema_union": schema_union,
        "config_sha256": sha256_file(config.config_path),
        "inputs": inputs,
        "outputs": {
            "ena_latest": {
                "path": str(paths.output.resolve()),
                "sha256": sha256_file(paths.output),
                "row_count": latest_count,
                "columns": columns,
                "row_order": "descending source precedence; source input order",
            },
            "ena_changes": {
                "path": str(paths.changes.resolve()),
                "sha256": sha256_file(paths.changes),
                "row_count": change_count,
                "removed_run_count": removed_count,
                "row_order": "run_accession, change_type, field",
            },
        },
    }
    paths.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
