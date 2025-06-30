Species identification
======================

Currently the only species identification consists of the information
in the ENA metadata, and from running sylph on the raw reads.


ENA species
-----------

This is simply the value of the ``scientific_name`` in the ENA metadata, which
is from the original submitter. It uses the NCBI taxonomy.


Sylph species
-------------

This uses the GTDB taxonomy, which is inconsistent with the ENA/NCBI
taxonomy.

Sylph calls were filtered for quality using the following information:

* ``c`` = the effective coverage ``Eff_cof`` column from Sylph
* ``k`` = kmer length used by Sylph, which for AllTheBacteria was the default 31
* ``g`` = reference genome size, which was obtained from GTDB metadata.
  We used column ``genome_size`` from ``https://data.ace.uq.edu.au/public/gtdb/data/releases/release214/214.0/bac120_metadata_r214.tar.gz``
  and ``https://data.ace.uq.edu.au/public/gtdb/data/releases/release214/214.1/ar53_metadata_r214.tsv.gz``
* ``r``: read length, which was calculated by dividing the ENA
  ``base_count`` by (2 * ``read_count``). Note that ``base_count`` is the total of
  bases across both paired FASTQ files, and ``read_count`` is the number of reads
  in one of the FASTQ files.


To pass, the Sylph call must have:

* ``c * g * (r / (r - k + 1)) >= 0.5 * base_count``, which roughly means that
  at least half of the reads match this genome. Although this may sound like a
  low threshold, sequencing errors have a significant detrimental affect on
  the calculation.
* ``Sequence_abundance`` at least 99.

Further, for a sample (not just a run) to pass, there can be only
one species call that passes. A sample with more than one run could have two
different species called, in which case it is failed. If all its passing run
calls have the same species, then the sample passes.

