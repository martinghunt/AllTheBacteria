# AllTheBacteria Release 1 metadata

AllTheBacteria Release 1 contains **3,092,278 bacterial genome assemblies**.
A genome assembly is a reconstruction of a genome from DNA sequencing reads.
These metadata files describe the assemblies, the samples and sequencing runs
they came from, their quality, and where to download the genome sequences.
They also contain information from the European Nucleotide Archive (ENA),
such as species, country, host and collection date, where available.

Release 1 is a complete collection. It brings together assemblies from earlier
AllTheBacteria releases and 344,425 assemblies from the 202603 increment,
which was based on the ENA snapshot taken on 17 March 2026.
You do not need to download the earlier releases to use Release 1.

## Which file should I use?

For most users, start with **`atb.metadata.r1.release_1_assembly_ena_metadata.tsv.gz`**.
This file contains every Release 1 sample, its selected ENA metadata, assembly
dataset, Sylph species assignment, high-quality filter and genome download URL.
Use the `assembly_sample_accession` column to find your samples.

A **sample accession** identifies a biological sample. A **run accession**
identifies a sequencing dataset. Most assemblies use one run, but some older
assemblies combine several runs from the same sample. Those samples have
several rows in the ENA metadata files, one per input run.

- ENA fields and download URLs for Release 1 samples:
  `atb.metadata.r1.release_1_assembly_ena_metadata.tsv.gz`
- Assembly records and download URLs for Release 1, without the full ENA fields:
  `atb.metadata.r1.release_1.tsv.gz`
- The database, for SQL queries and joining tables:
  `atb.metadata.r1.sqlite.xz`

The metadata files contain download links, rather than the genome sequences
themselves. See **Downloading genomes** below.

### File formats

Files ending in `.tsv.gz` are gzip-compressed, tab-separated text files. After
decompression, the first line contains column names and subsequent lines
contain data. Missing values are represented by `NA`.

For example, to decompress the recommended file while keeping the download:

```bash
gzip -dc atb.metadata.r1.release_1_assembly_ena_metadata.tsv.gz > release_1_metadata.tsv
```

`atb.metadata.r1.sqlite.xz` is the compressed SQLite database. Decompress it to
obtain `atb.metadata.r1.sqlite`, then open that file with a SQLite-compatible
tool. Unavailable fields in SQLite use SQL `NULL` values. Some original ENA
missing-value strings, such as `missing` or `NA`, are also retained.

## Choosing assemblies and downloading genomes

The `release_1` database view and its `atb.metadata.r1.release_1.tsv.gz` export
contain exactly the assemblies in this release. The recommended joined ENA
file is also restricted to Release 1.

The larger `assembly` table includes additional records, such as excluded
assemblies, failed attempts and samples that were not processed. When using
that table, select `in_release_1=1` to obtain the release assemblies.

Release 1 includes assemblies that do not pass the high-quality criteria.
To restrict your selection to high-quality assemblies, use `hq_filter='PASS'`
in the assembly table or `assembly_hq_filter='PASS'` in the joined ENA file.
This is an optional restriction, not the definition of release membership.
Likewise, `asm_pipe_filter='PASS'` does not define release membership: for
example, an assembly can be included even if its ENA assembly submission failed.
Submission failures have `ENA_ASM_SUBMIT_ERR` and a reason in `comments`.

### Downloading genomes

- **Individual genomes:** `aws_url` in the assembly tables, or
  `assembly_aws_url` in the joined ENA file, links to a gzip-compressed FASTA
  file (`.fa.gz`) for one assembly.
- **Multiple genomes:** `agc_filename`, `agc_url` and `agc_md5` in the assembly
  table identify the AGC archive containing each genome. AGC archives are
  compressed collections of assemblies; use AGC software to extract genomes.
  The `agc` table lists archive URLs, MD5 checksums, genome counts and sizes.

Release 1 AGC archives mix assemblies from the contributing datasets. There
is no separate set of AGC files containing only the new 202603 assemblies.
For that subset, select `dataset='Incr_release.202603'` and either download the
individual FASTA files or extract the selected samples from their AGC archives.

AWS provides the Release 1 genome downloads. OSF filenames and URLs are retained
as records of earlier releases. AGC MD5 checksums apply to the complete archive
files; individual FASTA gzip checksums are not included in these metadata.

## Understanding the ENA metadata

`assembly_ena_metadata` is the main database table for ENA fields associated
with assemblies. It contains one row per assembly/input-run pair, including
assemblies outside Release 1. Join to `release_1` when you need release members
only. The recommended joined file already makes this selection for you.

ENA records can change over time. This table contains a selected full ENA
record for each pair, rather than always using the newest record. Where the
exact processing-time record is unavailable, a suitable retained record is
used and its basis is labelled. The fields are taken together from that source;
individual missing fields are not filled from other snapshots.

Useful columns are:

- `assembly_sample_accession`: the AllTheBacteria assembly's sample identifier;
  use this to match your samples.
- `sample_accession`: the sample identifier or identifiers reported by the
  selected ENA record.
- `run_accession`: the run recorded as input to the assembly.
- `ena_run_accession`: the accession of the selected ENA run record. Usually
  the same as `run_accession`; two verified accession aliases are recorded
  in `run_alias`.
- `source_id`, `observed_date`: the selected metadata source and observation
  date, where known.
- `selection_basis`, `notes`: how the record was selected, including historical
  inference, fallbacks or unavailable metadata.
- `association_match`: whether the selected ENA record lists the assembly's
  sample. `0` means a conflicting association; unavailable comparisons are NULL.

Do not treat sample attributes from a record with `association_match=0` as
verified metadata for the assembly sample. A matching association can still
list several samples; it does not establish unique sample identity across
the complete history.

Missing metadata remain missing. Three release assemblies have no recoverable
run accession and therefore no row in `assembly_ena_metadata`. They are retained
in the recommended joined file, with unavailable ENA fields. A SQL `LEFT JOIN`
also retains these assemblies, as in the example below.

## Using the SQLite database

List release sample accessions and individual genome download URLs:

```sql
SELECT sample_accession, aws_url FROM release_1;
```

Obtain ENA fields for your samples, retaining samples with missing ENA records.
Replace the example accessions with your own:

```sql
SELECT a.sample_accession, e.run_accession, e.country, e.host,
       e.collection_date, e.selection_basis, e.association_match
FROM release_1 a
LEFT JOIN assembly_ena_metadata e
  ON e.assembly_sample_accession=a.sample_accession
WHERE a.sample_accession IN ('SAMN50311804', 'SAMN11953943');
```

Select the new 202603 assemblies, or select high-quality release assemblies:

```sql
SELECT sample_accession, aws_url FROM release_1
WHERE dataset='Incr_release.202603';

SELECT sample_accession, aws_url FROM release_1
WHERE hq_filter='PASS';
```

Select *Escherichia coli* assemblies with matching ENA metadata reporting the
United Kingdom. `EXISTS` returns each assembly once even if it used several runs:

```sql
SELECT a.sample_accession, a.aws_url FROM release_1 a
WHERE a.sylph_species='Escherichia coli' AND EXISTS (
  SELECT 1 FROM assembly_ena_metadata e
  WHERE e.assembly_sample_accession=a.sample_accession
    AND e.association_match=1 AND e.country LIKE 'United Kingdom%'
);
```

## Database tables and supporting history

Most users need only `release_1`, `assembly_ena_metadata`, and optionally `agc`.
The remaining tables provide quality measurements, run checks and traceability.
Each table or view has a matching `atb.metadata.r1.<name>.tsv.gz` download.
The joined ENA download is generated by a query rather than a stored view.

- `assembly`: all 3,720,978 known sample records, assembly identifiers, QC filters
  and download links.
- `release_1`: the 3,092,278 assembly records included in Release 1.
- `assembly_ena_metadata`: full selected ENA fields for assembly/input-run pairs.
- `agc`: AGC archive filenames, URLs, MD5 checksums, genome counts and sizes.
  Join using `agc_filename`.
- `assembly_stats`, `checkm2`, `sylph`: assembly statistics, CheckM2 quality
  estimates and Sylph species assignments.
- `run`: all 3,986,805 known runs, their metadata checks and snapshot-presence
  information.
- `ena_run_history`: latest known sample/read values and change summaries for
  each known run.
- `ena_source`: descriptions and hashes identifying the retained ENA sources.
- `ena_observation`: compact history of run/sample associations, read
  checksums/counts and technical fields across sources.
- `assembly_run_provenance`: evidence about the input runs and metadata
  associated with assembly processing.
- `run_alias`: two verified secondary-to-primary run accession links, with dated
  NCBI evidence. Descriptive fields still come from retained ENA records.
- `release`, `release_membership`: release identity and the sample accessions
  included in it.
- `release_provenance`: release policies and source-file hashes recorded during
  construction.
- `sylph_unlinked`: historical Sylph records with unresolved sample identifiers
  or ambiguous sample lists; these are not individual-assembly QC assignments.

### Latest values and historical evidence

`ena_run_history` contains `run_accession`, `latest_sample_accession`,
`latest_fastq_md5`, `latest_fastq_bytes`, `latest_base_count`, `latest_fastq_ftp`,
`latest_read_count`, and `atb_*` fields describing retained observations and
changes. A run absent from the latest snapshot retains its latest known values.
An `atb_ever_*` flag means a change occurred somewhere in the retained history;
it does not necessarily mean the selected assembly metadata differ from the
latest values.

For example, compare selected and latest FASTQ checksums:

```sql
SELECT e.assembly_sample_accession, e.run_accession, e.ena_run_accession,
       e.fastq_md5, h.latest_fastq_md5, h.atb_ever_fastq_md5_changed
FROM assembly_ena_metadata e
LEFT JOIN ena_run_history h ON h.run_accession=e.ena_run_accession
WHERE e.assembly_sample_accession='SAMN11953943';
```

Descriptive metadata and processing evidence serve different purposes.
`assembly_ena_metadata` may supply a richer historical record, while
`assembly_run_provenance` records what is known about processing. Its
`evidence_basis` distinguishes captures, selection snapshots, historical
inference and unavailable evidence. `processing_read_identity_match` in the
ENA metadata table compares selected MD5, byte count and base count with the
linked processing observation; that observation may itself be an inference.

Exact processing metadata are not available for every historical assembly.
For example, the original 2023 ENA dump is unavailable, and r0.2's processing
snapshot is not inferred from its dataset label. Unknown links and dates remain
NULL. The two verified accession aliases do not establish processing-time
metadata or identical FASTQ bytes.

`atb.metadata.r1.run_alias.evidence.01.xml` contains the raw NCBI response
supporting those two accession links.

## Additional ENA downloads

These two files provide additional metadata outside the SQLite database:

- **`atb.metadata.r1.ena_latest.tsv.gz`** contains full latest-known ENA records
  for all 3,986,805 known runs, including runs without a release assembly.
  Runs absent from the current snapshot retain their latest older record.
  Use this for the wider run population or full latest descriptive fields;
  use `assembly_ena_metadata` for the selected assembly-associated records.
- **`atb.metadata.r1.ena_661k_sample.tsv.gz`** contains all 661,405 rows and
  columns of the original File9 sample metadata for the 661k dataset, including
  country, host and collection date. Match `sample_id` to assembly sample
  accessions. Run-specific fields can aggregate several runs and should not
  be interpreted as individual-run records.

Complete historical ENA snapshots are not embedded in SQLite. `ena_observation`
retains selected history fields, and `ena_source` identifies the sources.
Some source-table names refer to archived databases, rather than tables in
this SQLite file.

## Checking your downloads

`SHA256SUMS` and `MD5SUMS` contain checksums for the distributed files. Use them
to check that your downloads are complete and unchanged. From the directory
containing your downloads, for example:

```bash
shasum -a 256 -c SHA256SUMS
```

If you downloaded only some of the files, the command will also report missing
files; inspect the results for the files you downloaded.

`atb.metadata.r1.sqlite.report.json` records source/output hashes, exported row
counts and the query used for the joined ENA download. The distributed
compressed database was checked to reproduce the canonical SQLite bytes when
decompressed. The checksum manifests do not include the decompressed SQLite
file or the manifests themselves.
