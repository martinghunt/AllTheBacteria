# Release 1 provenance extension

## Immutable data

Do not change assembly membership, assembly rows, QC, run checks, or AGC
assignments. Record table-content fingerprints before and after. Preserve a
compressed pre-extension database outside the publication directory until the
new database, exports, and checksum manifests validate.

## New tables

- `release`: ID, name, nullable publication date, description.
- `release_membership`: `(release_id, sample_accession)`; copy Release 1
  membership exactly from `assembly.in_release_1=1`.
- `ena_source`: stable ID, description, source kind, nullable snapshot date,
  date basis, original filename/checksum, nullable verified public URL, source
  table where a snapshot is embedded in an archived SQLite, notes.
- `ena_observation`: unique ID; source/run/observation date; reported sample
  association, FASTQ URLs/MD5/bytes, base/read counts, instrument platform,
  library strategy/source/layout. Retain identical observations across sources.
- `assembly_run_provenance`: assembly/run, nullable observation used,
  evidence basis and notes. Explicitly represent missing run provenance.
- `assembly_ena_metadata`: one row per assembly/run, the union of full ENA columns from
  the chosen source, selected observation/source/date, selection basis,
  association-match flag and notes. Preserve selected source records intact.
- `run_alias`: reviewed recorded-to-primary run accession links, matching
  assembly sample, selected ENA observation, and dated NCBI evidence with
  request URL and a checksum-verified accompanying response file.
- `ena_run_history`: run accession, latest sample/read values, and all existing
  `atb_*` history/change fields. Full `ena_latest` remains a separate download.

## Sources

Use the five ENA tables in the archived 202505 database: 661k published
metadata, 20240625, 20240801, 20250506, and the dated 202505 processing records.
Use the current 20260317 snapshot (the current rows in the canonical ENA table,
restricted to that snapshot's original columns) and captured 202603 processing
records. Do not invent a date for the 661k source or missing 2023 dump.
Source archives stay separate; source URLs remain NULL until verified.

Also preserve `Figshare_661k/File9_all_metadata_ena_661K.txt` as the full
`ena_661k_sample` separate download, registered as published sample-summary metadata. It
contains original country/host/collection data but aggregates multiple runs;
do not manufacture run-level observations by splitting its aggregated fields.
Users can match it to assemblies by `sample_id` for original 661k
sample metadata. The normalized JSON-derived source supplies run-level evidence.

## Selection policy

Prefer source records whose reported sample set includes the assembly sample.
For 202603 prefer the actual processing capture; for 202505 prefer the dated
processing metadata; for 202408 prefer its selection snapshot. For 661k use
published source metadata as historical inference. r0.2 has no uniformly proven
processing snapshot: select the earliest retained association-matching evidence
as an explicitly inferred descriptive record, without asserting it was used.
Other matching records are descriptive fallbacks. If no matching record exists,
select the latest retained evidence with an explicit association-conflict flag
and warning. No observations yields a NULL metadata row and unavailable basis.
Full-row metadata is never assembled field-by-field from different snapshots.

For the two explicitly verified NCBI SRA secondary-accession links, retain the
original run accession in assembly and processing provenance. Select the full
retained ENA primary-accession record as `accession_alias_fallback`, without
asserting processing-time metadata or FASTQ byte identity. In `assembly_ena_metadata`,
`run_accession` remains the recorded input and `ena_run_accession` identifies
the selected ENA record for all available rows. NCBI supplies alias evidence,
not replacement descriptive metadata. Preserve all membership and QC values.

Source inspection found that the fixed 661k run table lacks `country`.
Processing JSON records have variable keys and include country where supplied;
absence from one record does not imply absence from the whole capture source.
The descriptive selection prefers association-matching sources with country
in their schema over the fixed sparse 661k run table. Actual processing records
remain preferred when their source supports descriptive metadata, with absent
fields left NULL. Processing evidence remains independently linked in
`assembly_run_provenance`. Record whether descriptive-row read identity matches
the processing observation; do not imply they are the same record.

## Outputs and validation

Export all new tables as compressed TSVs. Retain existing flat-file exports.
Document multiple rows per assembly and distinguish selected descriptive
metadata from evidence of the processing-time record. Validate all foreign
references, coverage of every Release 1 member, multi-run handling, source
counts, selected full-row consistency, and immutable table fingerprints.
Recompress SQLite, refresh the portable public report, and regenerate matching
MD5/SHA-256 manifests. Report source gaps and selection counts explicitly.
