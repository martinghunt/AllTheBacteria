# AllTheBacteria release metadata

This directory contains the scripts used to prepare the metadata for an
AllTheBacteria release. Metadata describes each genome assembly: which sample
and sequencing reads it came from, its quality checks, and where its files can
be downloaded. These scripts prepare those records; they do not assemble the
genomes themselves.

The aim is to make releases reproducible and easier to check. Information in
the European Nucleotide Archive (ENA) can change after an assembly was made:
records may be updated or removed, and links between samples and sequencing
runs may change. Here, a sample is the biological material being sequenced,
and a run is a set of sequencing reads. Some older assemblies used several
runs from one sample.

The tools bring together previous release metadata, saved ENA snapshots and
results from assembly and quality checks. They help to:

- track new and changed ENA records while retaining older information;
- apply release filters and identify duplicate assemblies;
- record which assemblies belong to a release and where their metadata came from;
- build the release database, tab-separated metadata files and AWS download index.

This directory is mainly for people preparing a release or checking how one
was made. To use the published genomes and metadata, start with the
[AllTheBacteria documentation](https://allthebacteria.org/docs/). The sections
below describe the individual tools and their inputs. Release-specific notes
are in `releases/`.

## AWS file catalogue and bucket checksums

Build a master AWS file index, a separate individual-FASTA index, and root
`MD5SUMS` manifests with:

```bash
python3 -m atb_release_metadata.aws_file_index \
  --osf-index all_atb_files.tsv \
  --aws-lists aws_file_lists \
  --assembly-db release_1/atb.metadata.r1.sqlite \
  --cache-dir aws_metadata_build/cache \
  --work-db aws_metadata_build/objects.sqlite \
  --output-dir aws_metadata \
  --lexicmap-md5 lexic.2024.snapshot.md5
```

`aws_file_lists` contains the supplied `buckets` file and recursive S3 listings
named `<bucket>.list`. The assemblies bucket may be omitted from those listings:
its latest available S3 inventory is used, supplemented by known FASTA URLs in
the release database. Retain the cache and input listings to reproduce this
particular build. The newest assembly inventory among the supplied listings is
selected; changing the listings can select a different inventory. Automated
objects under `allthebacteria-assemblies/assemblies-list/` in the metadata
bucket are used as build inputs, then excluded from the published file
catalogues and bucket MD5SUMS.

Checksums come from unambiguous, size-matching OSF records, published checksum
manifests, or downloaded small files. Large data files are not rehashed.
Individual assemblies have no generated MD5 manifest; their catalogue MD5
values are `NA`. The optional `--lexicmap-md5` input is a fixed copy of the
checksum-job output. Only paths containing `/2024-08/` are mapped to the
`202408/` AWS prefix. All listed LexicMap objects must be covered; unlisted
source files are reported and ignored. Without this input, LexicMap is also
excluded from MD5 manifests. Capture the input before use if the checksum job
is still appending other releases. ETags are kept separately and never treated as MD5s.
`all_atb_files.tsv` excludes the individual FASTAs, which are listed in
`assembly_files.tsv`. Both have exactly these columns: `filename`, `url`,
`md5`, `size_bytes`, `size(MB)`, `bucket`. The MB column uses MiB
(1,048,576 bytes), rounded to two decimal places for compatibility with the
old index. Each TSV also has an identical gzip level 9 version.
The output report records input hashes and checksum policies. Upload each
generated `<bucket>/MD5SUMS` to that bucket's root before publishing the
catalogue, which includes these generated files. The tool performs no uploads.

## ENA release metadata

This directory contains correctness-first tools for making reproducible
AllTheBacteria metadata releases. The first implemented stage builds one
latest-known ENA record per run while retaining records for runs that disappear
from later ENA snapshots.

The implementation uses only the Python standard library. A temporary SQLite
database bounds memory use and provides uniqueness constraints. Inputs and
outputs may be plain TSV, `*.tsv.gz`, or `*.tsv.xz` files.

## ENA latest-known table

The builder accepts either:

* a historical set of ENA sources, to bootstrap the first `ena_latest`; or
* the previous release's `ena_latest` plus a new complete ENA snapshot.

Every source is parsed using its own header. The output schema is the union of
the ENA columns: columns from the current snapshot retain their order, followed
by historical-only columns in lexical order. An absent source column is emitted
as an empty TSV value. Empty values are not filled from older records, because
each output row must represent one real ENA record rather than a synthetic
mixture of snapshots.

The newest complete record for a run wins. Runs absent from the current
snapshot retain their latest older record and have
`atb_present_in_current_snapshot=0`. `atb_*` columns record provenance and
cumulative critical-field consistency state.

The builder also creates:

* a deterministic change ledger for new, removed, reappeared, reassigned, and
  critical-FASTQ-changed runs; and
* a JSON manifest containing schemas, row counts, file sizes, and SHA-256
  hashes.

## Configuration

See `example.bootstrap-config.json` and `example.increment-config.json`.
Paths are resolved relative to the configuration file.

Sources must be listed from older to newer, and the current complete snapshot
must be last. A source may have a fixed `date` or a per-record `date_column`.
Both may be omitted when the acquisition date of a historical source is
genuinely unknown; the provenance date is then empty rather than fabricated.
Give every source a unique integer `precedence`, and list sources in increasing
precedence order. This makes the intended source ordering explicit even when an
old source's exact acquisition date is unknown. The current snapshot must have
the greatest precedence. A fixed `date` or per-row `date_column` records when
the observation was made, but does not silently decide precedence.

`exclude_columns` removes source bookkeeping fields such as `obtained_date`
from the ENA schema. A per-record `date_column` is excluded automatically.

## Running

From this directory:

```bash
python3 -m atb_release_metadata build-ena-latest \
  --config release-config.json \
  --work-db work/ena_latest.sqlite \
  --output output/atb.metadata.YYYYMM.ena_latest.tsv.xz \
  --changes output/atb.metadata.YYYYMM.ena_changes.tsv.xz \
  --manifest output/atb.metadata.YYYYMM.ena_latest.manifest.json
```

Existing work or output files are never overwritten. The work database is
retained for inspection. Build in a new staging directory for each attempt.
The builder reads the compressed sources twice so that the SQLite work database
only stores compact audit state, not hundreds of full ENA columns.

## Strict validation

The build fails on:

* empty or duplicate column names;
* a data row with a different field count from its own header;
* an empty run accession;
* a duplicate run/date observation within a source (a partial historical
  source may legitimately contain the same run on distinct dates);
* absent required columns in the current complete snapshot;
* input columns colliding with reserved `atb_*` columns;
* invalid observation dates; or
* ambiguous source precedence.

Unusual values, including semicolon-separated sample accessions, are retained
and reported rather than silently skipped.

Run the fixture tests with:

```bash
python3 -m unittest discover -s tests -v
```

## Scope

ENA observation merging is kept separate from release policy so that both can
be audited and regression-tested independently.

## Validating the 661k inputs

`Json1_ENA_metadata.json.gz` contains the individual run records selected for
the 661k assemblies. `File3_metadata_661K.txt` and
`File9_all_metadata_ena_661K.txt` contain one row per sample; File9 collapses
multiple runs into comma-separated fields and is therefore not a safe per-run
ENA source.

Validate the sample censuses and compare the JSON with the normalized
`ena_661k` table field-for-field:

```bash
python3 -m atb_release_metadata validate-661k \
  --json Json1_ENA_metadata.json.gz \
  --file3 File3_metadata_661K.txt \
  --file9 File9_all_metadata_ena_661K.txt \
  --ena-table atb.metadata.202505.ena_661k.tsv.xz \
  --report validate_661k.json
```

## Run table

After validating `ena_latest`, update the previous release's run table:

```bash
python3 -m atb_release_metadata build-run-table \
  --previous-run atb.metadata.PREVIOUS.run.tsv.xz \
  --ena-latest atb.metadata.YYYYMM.ena_latest.tsv.xz \
  --release YYYYMM \
  --current-snapshot-column in_ena_YYYYMMDD \
  --work-db work/run.sqlite \
  --output atb.metadata.YYYYMM.run.tsv.xz \
  --report atb.metadata.YYYYMM.run.report.json
```

Historical failures are monotonic: a run that failed in the previous release
does not silently become usable. New current failures are added for removal,
sample reassignment or ambiguity, critical FASTQ changes or missing values, and
metadata-policy failures. The historical sample-accession set is retained for
reassigned runs. Machine-readable `failure_reasons` are emitted separately from
the preserved human-readable comments.

`run.pass` means that a run is suitable for strict current ATB processing. The
661k allowance for concatenating multiple eligible runs belongs to historical
assembly provenance; it does not weaken current run eligibility.

All prior `in_ena_*` census columns are discovered from the previous table and
carried forward. The named current-snapshot column is appended, so a future
release does not require editing a hard-coded list of snapshot columns.

## Assembly candidate plan

Derive what would be assembled now from the updated run table, independently
of any historical job list:

```bash
python3 -m atb_release_metadata plan-assemblies \
  --previous-assembly atb.metadata.PREVIOUS.assembly.tsv.xz \
  --run-table atb.metadata.YYYYMM.run.tsv.xz \
  --executed to_run.YYYYMMDD.txt \
  --work-db work/assembly-plan.sqlite \
  --output assembly-candidates.YYYYMM.tsv.xz \
  --report assembly-candidates.YYYYMM.report.json
```

A candidate must not already occur in the previous assembly table and must
have exactly one currently passing run. `--executed` is optional and is only
used for comparison: it never changes the derived candidate set. The report
lists candidate-only, executed-only, and differing-run samples so that drift
between selection time and release time is explicit.

## Optional current NCBI alias audit

```bash
PYTHONPATH=github_metadata_release python3 -m atb_release_metadata.ncbi_alias_audit \
  --database release_1/atb.metadata.r1.sqlite \
  --output-dir 202603_metadata_build_v2/ncbi_alias_audit.20261110
```

This read-only audit batches missing-record accessions, limits request starts
to at most one per second, caches raw API responses and request provenance,
and reports explicit primary/secondary RUN aliases with separate BioSample
checks. Reruns reuse the cache; use a new directory for a fresh audit date.
No search hit is not proof of suppression. The audit does not alter the release
or substitute a new accession for the recorded assembly input.

## Assembly table

The final canonical Release 1 SQLite database and its direct TSV exports are
built from the frozen publication tables and historical QC database with:

```bash
PYTHONPATH=github_metadata_release python3 -m atb_release_metadata.sqlite_release \
  --workspace . --output-dir release_1
PYTHONPATH=github_metadata_release python3 -m atb_release_metadata.ena_history \
  --workspace . --output-dir release_1
PYTHONPATH=github_metadata_release python3 -m atb_release_metadata.ena_history_file9 \
  --workspace . --output-dir release_1
PYTHONPATH=github_metadata_release python3 -m atb_release_metadata.apply_run_aliases \
  --output-dir release_1 \
  --audit-dir 202603_metadata_build_v2/ncbi_alias_audit.20261110
PYTHONPATH=github_metadata_release python3 -m atb_release_metadata.publication_schema \
  --output-dir release_1 --work-dir 202603_metadata_build_v2/publication_schema
PYTHONPATH=github_metadata_release python3 -m atb_release_metadata.release_query_exports \
  --output-dir release_1
xz -T4 -3 -k release_1/atb.metadata.r1.sqlite
PYTHONPATH=github_metadata_release python3 -m atb_release_metadata.release_checksums \
  --output-dir release_1
```

Run this from the release workspace. The output directory must not already
contain a release database. `assembly` retains all known samples; the
`release_1` view restricts it to published members. QC tables combine the
historical database with the corrected new inputs. The JSON report records
input hashes, output hashes, and counts for every database-derived export.
Original ENA snapshots remain archived inputs; `assembly_ena_metadata` is the
main public ENA table. `ena_run_history` contains compact latest values and all
existing `atb_*` history flags. Full `ena_latest` and original `ena_661k_sample`
remain separate downloads, recorded under `separate_files` in the report.
Treat the published database as frozen and rebuild it and its exports
together whenever inputs change.

Public reports and database provenance identify files by basename and hash;
they do not contain build-machine paths. The checksum finalizer verifies the
compressed database and writes `MD5SUMS` for the publication files. The uncompressed SQLite stays available locally; publish
its compressed copy. The manifest excludes the uncompressed file and includes
the two separate ENA downloads. The versioned
`atb_release_metadata/release_1_README.md` template is
copied into the publication directory by the schema stage.
If the public JSON report is edited to add verified source
URLs, rerun the finalizer to refresh the manifest.

The history extension preserves frozen assembly/QC/run/AGC table fingerprints.
`assembly_run_provenance` records processing evidence where established;
`assembly_ena_metadata` supplies intact best-available descriptive rows per assembly/run,
explicitly identifying inferences, missing metadata, and association conflicts.
The separate `ena_661k_sample` file preserves File9's original sample-level metadata; its
aggregated multi-run fields are not interpreted as run-level observations.

For the two reviewed NCBI secondary-accession mappings, run
`python3 -m atb_release_metadata.apply_run_aliases --output-dir release_1
--audit-dir 202603_metadata_build_v2/ncbi_alias_audit.20261110` after the history
and File9 extensions, before publication-schema simplification, the joined
export and checksum finalization.
This consumes checksum-verified archived evidence without network requests.
`run_alias` retains that dated evidence, and `assembly_ena_metadata.ena_run_accession`
identifies the selected ENA record while `run_accession` preserves the original
assembly input. The two descriptive fallbacks use full retained ENA rows and
are labelled `accession_alias_fallback`; processing-time evidence, membership,
QC, and AGC assignments remain unchanged. Regenerate the joined export and
compressed SQLite before running the checksum finalizer.

After ENA submission, apply outcomes with
`python3 -m atb_release_metadata.ena_submission --assembly INPUT --status STATUS
--output OUTPUT --report REPORT`. The status file must cover exactly the
202603 rows with `in_release_1=1`. Failures receive `ENA_ASM_SUBMIT_ERR` in
`asm_pipe_filter` and an error comment; their accession is `NA`. Frozen release
membership and HQ status remain unchanged. Therefore use `in_release_1=1`,
with the desired dataset, for release selection after publication enrichment.

Release 1 AWS publication enrichment is performed with
`python3 -m atb_release_metadata.publication`; see
[`releases/202603.md`](releases/202603.md) for the full command and provenance.
It writes a new assembly table and an AGC archive table after reconciling the
complete release membership with the sample/archive mapping and archive MD5s.
`agc_md5` checks the exact archive file. AWS is the Release 1 download service;
OSF columns are retained solely as historical publication information.

`build-assembly-table` preserves every previous assembly row, refreshes its
run-derived metadata filters, adds every actually processed sample as
`Incr_release.YYYYMM`, and adds all other newly known samples as
`not_processed`. The execution list is treated as provenance, not silently
replaced by the clean-slate candidate list. Process-time ENA metadata is
compared with `ena_latest`, and differences are reported. A changed process-time
FASTQ MD5 or byte count adds the established `FASTQ_MD5_CHANGED` and/or
`FASTQ_BYTES_CHANGED` filter to the new assembly, with an audit comment.

The builder checks exact agreement among the execution list, process-time
metadata, status, Sylph/no-match census, assembly statistics, CheckM2, and
seqkit inputs. New publication identifiers and URLs are emitted as `TODO`, and
`asm_fasta_on_osf` remains zero until publication. HQ defaults are recorded in
code: Sylph abundance 99%, CheckM2 completeness 90%, contamination 5%, total
length 100 kbp--15 Mbp, 2,000 contigs, and N50 2,000.

For Release 1, the assembly table additionally contains `in_release_1`.
It retains published legacy artifacts (`asm_fasta_on_osf=1`) from 661k, r0.2,
202408, and 202505 regardless of later metadata failures, except duplicate
non-keepers. New 202603 rows are included only when their complete
`asm_pipe_filter` is `PASS`. This release-membership flag is intentionally not
duplicated in run metadata, because it describes an assembly artifact rather
than an ENA run.

Across the complete historical-plus-new assembly table, every non-empty seqkit
sum shared by two or more samples receives a `duplicate_seqkit_sum_status` and
an audit comment listing the other matching sample/run accessions. One member
per group is `KEEP_OLDEST`, chosen by earliest ENA `first_public` then natural sample
accession order; it remains eligible. All other members are
`LIKELY_DUPLICATE` and receive `DUP_SEQKIT_SUM` in both `asm_pipe_filter` and
`hq_filter`. This is deliberately an assembly-level decision: it does not make
the ENA run metadata invalid.

Pass `--release YYYYMM` and
`--current-snapshot-column in_ena_YYYYMMDD` to this command as well. They
control the new dataset label, audit comments, and which run-table census
column represents the current ENA snapshot.

If the assembly report identifies old assembly-provenance runs for which no
retained ENA record exists, export their one-to-one sample mapping with:

```bash
python3 -m atb_release_metadata export-historical-assembly-run-gaps \
  --assembly atb.metadata.YYYYMM.assembly.tsv.xz \
  --assembly-report atb.metadata.YYYYMM.assembly.report.json \
  --output historical_assembly_runs_absent_from_run_table.tsv
```

## Likely duplicate assemblies

To retain the oldest ENA accession for each non-empty repeated seqkit sum while
making every later row reviewable, export a report rather than deleting or
changing any assembly filter:

```bash
python3 -m atb_release_metadata export-duplicate-assembly-report \
  --assembly atb.metadata.YYYYMM.assembly.tsv.xz \
  --ena-latest atb.metadata.YYYYMM.ena_latest.tsv.xz \
  --dataset Incr_release.YYYYMM \
  --output likely_duplicate_assemblies.YYYYMM.tsv
```

Within each seqkit group, `KEEP_OLDEST` is selected by earliest ENA
`first_public`, then natural sample-accession order. Other
rows are marked `LIKELY_DUPLICATE`; the report includes FASTQ URLs, checksums,
sizes, and read/base counts for direct verification.

When a current ENA metadata extract has been retrieved for those rows, compare
each exact-assembly group without treating run-specific fields (accessions and
FASTQ paths) as substantive differences:

```bash
python3 -m atb_release_metadata compare-duplicate-ena-metadata \
  --duplicate-report likely_duplicate_assemblies.YYYYMM.tsv \
  --ena-metadata likely_duplicate_assemblies.YYYYMM.ena_metadata.YYYYMMDD.tsv \
  --output likely_duplicate_assemblies.YYYYMM.ena_metadata.comparison.tsv
```

The group report records all differing study, descriptive, library, centre,
read/base-count, collection-date, and scientific-name values. It specifically
flags groups where the selected newest accession has a 1905 collection date
despite a non-1905 alternative, and the converse.
