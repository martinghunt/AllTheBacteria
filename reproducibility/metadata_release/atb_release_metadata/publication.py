"""Enrich the frozen Release 1 membership with published AWS locations."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from .io import iter_tsv_dicts, open_tsv_writer, read_header, sha256_file

FASTA_ROOT = "https://allthebacteria-assemblies.s3.eu-west-2.amazonaws.com/"
AGC_ROOT = "https://allthebacteria-agc.s3.eu-west-2.amazonaws.com/release.1/"


def publish(assembly: Path, mapping: Path, batches: Path, md5: Path,
            output: Path, archives: Path, report: Path) -> dict:
    for path in (output, archives, report):
        if path.exists():
            raise ValueError(f"refusing to overwrite {path}")
    header = read_header(assembly)
    required = {"sample_accession", "in_release_1", "aws_url", "dataset", "osf_tarball_filename", "osf_tarball_url"}
    if not required.issubset(header):
        raise ValueError("assembly table missing required columns")
    extra = ("agc_filename", "agc_url", "agc_md5")
    if set(extra) & set(header):
        raise ValueError("assembly already enriched")
    checksums = {}
    for line in md5.read_text().splitlines():
        digest, filename = line.split()
        if not re.fullmatch(r"[0-9a-f]{32}", digest) or not re.fullmatch(r"atb\.assembly\.r1\.part[0-9]+\.agc", filename):
            raise ValueError(f"invalid checksum entry: {line}")
        if filename in checksums:
            raise ValueError(f"duplicate checksum: {filename}")
        checksums[filename] = digest
    members = {}
    counts = Counter()
    for _, row in iter_tsv_dicts(mapping):
        sample, batch = row["accession"], row["batch_name"]
        if sample in members or batch + ".agc" not in checksums:
            raise ValueError(f"duplicate sample or missing archive checksum: {sample}/{batch}")
        members[sample] = batch
        counts[batch] += 1
    archive_rows = {}
    for _, row in iter_tsv_dicts(batches):
        batch = row["batch_name"]
        if batch in archive_rows or counts[batch] != int(row["genome_count"]):
            raise ValueError(f"batch count mismatch: {batch}")
        if int(row["size_bytes"]) <= 0:
            raise ValueError(f"invalid archive size: {batch}")
        archive_rows[batch] = row
    if set(archive_rows) != set(counts) or {b + ".agc" for b in counts} != set(checksums):
        raise ValueError("mapping, batches, and checksums describe different archive sets")
    # Check the entire membership set before creating outputs.
    seen = set()
    for _, row in iter_tsv_dicts(assembly):
        sample = row["sample_accession"]
        if row["in_release_1"] not in {"0", "1"}:
            raise ValueError(f"invalid release flag: {sample}")
        if row["in_release_1"] == "1":
            if sample in seen or sample not in members:
                raise ValueError(f"duplicate or unmapped Release 1 sample: {sample}")
            seen.add(sample)
    if seen != set(members):
        raise ValueError(f"AGC mapping has {len(set(members) - seen)} non-release samples")
    datasets = Counter()
    with open_tsv_writer(output) as writer:
        writer.writerow(header + list(extra))
        for _, row in iter_tsv_dicts(assembly):
            sample = row["sample_accession"]
            row.update(dict.fromkeys(extra, "NA"))
            if row["in_release_1"] == "1":
                filename = members[sample] + ".agc"
                row.update(aws_url=FASTA_ROOT + sample + ".fa.gz",
                           agc_filename=filename, agc_url=AGC_ROOT + filename,
                           agc_md5=checksums[filename])
                datasets[row["dataset"]] += 1
            if row["osf_tarball_url"] == "TODO":
                row["osf_tarball_url"] = "NA"
            if row["osf_tarball_filename"] == "TODO":
                row["osf_tarball_filename"] = "NA"
            writer.writerow([row[c] for c in header + list(extra)])
    with open_tsv_writer(archives) as writer:
        writer.writerow(("agc_filename", "agc_url", "md5", "genome_count", "size_bytes"))
        for batch, row in sorted(archive_rows.items()):
            filename = batch + ".agc"
            writer.writerow((filename, AGC_ROOT + filename, checksums[filename], row["genome_count"], row["size_bytes"]))
    result = {"status": "PASS", "release_1_rows": len(seen), "archive_count": len(counts),
              "dataset_counts": dict(datasets),
              "inputs": {str(p): sha256_file(p) for p in (assembly, mapping, batches, md5)},
              "outputs": {str(p): sha256_file(p) for p in (output, archives)},
              "verification": "Local manifests reconciled; remote object contents not verified."}
    report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("assembly", "mapping", "batches", "md5", "output", "archives", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    print(json.dumps(publish(**vars(parser.parse_args())), indent=2))
