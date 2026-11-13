# AllTheBacteria Release 1

Please note that this README is an overview of the metadata files,
intended to be a quick start guide.
The full documentation is here: https://allthebacteria.org/docs/

AllTheBacteria Release 1 contains 3,092,278 bacterial genome assemblies.
These metadata files describe the assemblies, the samples and sequencing runs
they came from, their quality, and where to download the genome sequences.
They also contain information from the European Nucleotide Archive (ENA),
such as species, country, host and collection date, where available.

Release 1 is a complete collection. It brings together assemblies from earlier
AllTheBacteria releases and 344,425 assemblies based on an ENA snapshot taken
on 17 March 2026.  You do not need to download the earlier releases to use
Release 1.

## Assemblies

We recommend the AllTheBacteria command line tool for querying metadata and
downloading assemblies: https://allthebacteria.org/docs/cli/

Assemblies are available from AWS in two formats:

1. Individual gzipped FASTA files, useful for downloading a few assemblies.
2. 1,129 AGC archives containing the complete release, useful for downloading
   the collection or large subsets. The archives mix assemblies from all
   contributing datasets. Use AGC software, or the command line tool, to
   extract the genomes you need.

Most assemblies are also accessioned in the ENA. Download URLs and ENA
accessions are included in the assembly metadata.

## Metadata

The main metadata database is `atb.metadata.r1.sqlite.xz`. It is approximately
2.8 GiB compressed and 21 GiB after decompression. We also provide gzipped,
tab-separated files (`.tsv.gz`) for use without SQLite:

- Start with `atb.metadata.r1.release_1.tsv.gz` for exactly the Release 1
  assemblies and their key metadata: sample/run accessions, species calls,
  quality filters, genome download URLs and AGC archive checksums.
- For ENA fields such as country, host and collection date, use
  `atb.metadata.r1.release_1_assembly_ena_metadata.tsv.gz`. Some older
  assemblies used several sequencing runs, so this file can have several
  rows for one sample.
- `atb.metadata.r1.agc.tsv.gz` lists the AGC archives, their URLs, sizes and
  MD5 checksums.

To select high-quality assemblies, use `hq_filter='PASS'` in the assembly
metadata, or `assembly_hq_filter='PASS'` in the joined ENA file. This is
optional: Release 1 includes assemblies that do not pass the HQ filters.

The full `assembly` table also contains records outside the release. Use
`in_release_1=1` to select release members; the two release-only files above
already make this selection. Missing fields in TSV files are written as `NA`.

Full latest-known ENA run metadata and the original 661k sample metadata are
provided as separate downloads. The website describes these and the other
supporting files: https://allthebacteria.org/docs/metadata_sqlite/

## Checksums

Use `MD5SUMS` to check downloaded files. AGC checksums in the
metadata apply to the complete archive files.
