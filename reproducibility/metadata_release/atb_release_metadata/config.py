from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_CURRENT_COLUMNS = (
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
)


def normalise_date(value: str, context: str) -> str:
    value = value.strip()
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"{context}: date must be YYYY-MM-DD or YYYYMMDD, got {value!r}")


@dataclass(frozen=True)
class Source:
    name: str
    path: Path
    kind: str
    precedence: int
    date: str | None
    date_column: str | None
    exclude_columns: tuple[str, ...]

    def date_for_row(self, row: dict[str, str], line_number: int) -> str:
        if self.date_column is None:
            return self.date or ""
        value = row[self.date_column]
        return normalise_date(value, f"{self.path}:{line_number}:{self.date_column}")


@dataclass(frozen=True)
class BuildConfig:
    release: str
    current_source: str
    sources: tuple[Source, ...]
    previous_latest: Path | None
    required_current_columns: tuple[str, ...]
    config_path: Path

    @property
    def current(self) -> Source:
        return next(source for source in self.sources if source.name == self.current_source)


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def load_config(path: Path) -> BuildConfig:
    path = path.resolve()
    raw = _require_mapping(json.loads(path.read_text(encoding="utf-8")), str(path))
    base = path.parent

    release = str(raw.get("release", "")).strip()
    current_source = str(raw.get("current_source", "")).strip()
    if not release or not current_source:
        raise ValueError("config requires non-empty release and current_source")

    raw_sources = raw.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("config requires a non-empty sources list")

    sources: list[Source] = []
    names: set[str] = set()
    for index, item_value in enumerate(raw_sources):
        item = _require_mapping(item_value, f"sources[{index}]")
        name = str(item.get("name", "")).strip()
        if not name or name in names:
            raise ValueError(f"sources[{index}]: source name is empty or duplicated: {name!r}")
        names.add(name)
        source_path = (base / str(item.get("path", ""))).resolve()
        if not source_path.is_file():
            raise ValueError(f"sources[{index}]: input does not exist: {source_path}")
        date_value = item.get("date")
        date_column_value = item.get("date_column")
        if date_value is not None and date_column_value is not None:
            raise ValueError(f"sources[{index}]: specify at most one of date or date_column")
        date = (
            normalise_date(str(date_value), f"sources[{index}].date")
            if date_value is not None
            else None
        )
        date_column = str(date_column_value) if date_column_value is not None else None
        if "precedence" not in item:
            raise ValueError(f"sources[{index}]: missing required precedence")
        exclude = tuple(str(x) for x in item.get("exclude_columns", []))
        if date_column is not None and date_column not in exclude:
            exclude += (date_column,)
        sources.append(
            Source(
                name=name,
                path=source_path,
                kind=str(item.get("kind", "complete")),
                precedence=int(item["precedence"]),
                date=date,
                date_column=date_column,
                exclude_columns=exclude,
            )
        )

    allowed_kinds = {"complete", "partial", "historical_reduced"}
    invalid_kinds = sorted({source.kind for source in sources}.difference(allowed_kinds))
    if invalid_kinds:
        raise ValueError(f"unknown source kind(s): {', '.join(invalid_kinds)}")

    previous_value = raw.get("previous_latest")
    previous = None if previous_value is None else (base / str(previous_value)).resolve()
    if previous is not None and not previous.is_file():
        raise ValueError(f"previous_latest does not exist: {previous}")

    if current_source not in names:
        raise ValueError(f"current_source {current_source!r} is not in sources")
    if sources[-1].name != current_source:
        raise ValueError("current_source must be the final source in the sources list")
    if sources[-1].kind != "complete":
        raise ValueError("current_source must have kind=complete")
    if sources[-1].date is None:
        raise ValueError("current_source must have a fixed date")
    precedences = [source.precedence for source in sources]
    if len(set(precedences)) != len(precedences):
        raise ValueError("every source must have a unique precedence")
    if precedences != sorted(precedences):
        raise ValueError("sources must be listed in increasing precedence order")
    if previous is not None and any(value <= 0 for value in precedences):
        raise ValueError("source precedence must be positive when previous_latest is used")

    required = tuple(raw.get("required_current_columns", REQUIRED_CURRENT_COLUMNS))
    return BuildConfig(
        release=release,
        current_source=current_source,
        sources=tuple(sources),
        previous_latest=previous,
        required_current_columns=required,
        config_path=path,
    )
