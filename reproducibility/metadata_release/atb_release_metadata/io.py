from __future__ import annotations

import csv
import gzip
import hashlib
import io
import lzma
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO


csv.field_size_limit(sys.maxsize)


class TsvFormatError(ValueError):
    """Raised when a TSV file cannot be parsed without loss or ambiguity."""


@contextmanager
def open_text(path: Path, mode: str = "rt") -> Iterator[TextIO]:
    if "t" not in mode:
        raise ValueError("open_text only supports text modes")
    if path.name.endswith(".xz"):
        handle = lzma.open(path, mode, encoding="utf-8", newline="")
    elif path.name.endswith(".gz"):
        handle = gzip.open(path, mode, encoding="utf-8", newline="")
    else:
        handle = path.open(mode, encoding="utf-8", newline="")
    try:
        yield handle
    finally:
        handle.close()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def read_header(path: Path) -> list[str]:
    with open_text(path) as handle:
        reader = csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration as error:
            raise TsvFormatError(f"{path}: empty file") from error
    validate_header(path, header)
    return header


def validate_header(path: Path, header: list[str]) -> None:
    if not header:
        raise TsvFormatError(f"{path}: empty header")
    empty = [str(index + 1) for index, value in enumerate(header) if value == ""]
    if empty:
        raise TsvFormatError(
            f"{path}: empty column name at position(s) {', '.join(empty)}"
        )
    duplicates = sorted({name for name in header if header.count(name) > 1})
    if duplicates:
        raise TsvFormatError(
            f"{path}: duplicate column name(s): {', '.join(duplicates)}"
        )


def iter_tsv_dicts(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield strict, unquoted TSV records as ``(line_number, row)`` pairs."""
    with open_text(path) as handle:
        reader = csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration as error:
            raise TsvFormatError(f"{path}: empty file") from error
        validate_header(path, header)
        expected = len(header)
        for line_number, fields in enumerate(reader, start=2):
            if len(fields) != expected:
                raise TsvFormatError(
                    f"{path}:{line_number}: expected {expected} fields from header, "
                    f"found {len(fields)}"
                )
            yield line_number, dict(zip(header, fields, strict=True))


@contextmanager
def open_tsv_writer(path: Path) -> Iterator[csv.writer]:
    with open_text(path, "wt") as handle:
        yield csv.writer(
            handle,
            delimiter="\t",
            lineterminator="\n",
            quotechar=None,
            quoting=csv.QUOTE_NONE,
            escapechar=None,
        )
