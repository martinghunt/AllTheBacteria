# Defense systems

Contributors: Liam Shaw, Rachel Wheatley, Robert A. Petit III

The initial aim of this analysis was to investigate defense systems within Enterobacterales. We ran [DefenseFinder](https://github.com/mdmparis/defense-finder) v1.3.0 on Enterobacterales species with >400 samples (27 species, n=1,166,956 samples) within AllTheBacteria release 0.2 plus incremental release 2024-08. The results are available on OSF [here](https://osf.io/hpgac/overview). 

By default, DefenseFinder produces three main outputs: `*genes.tsv`, `*hmmer.tsv` and `*systems.tsv`. We have shared a single file containing the concatenated results of `*systems.tsv`. This file contains results from 1,166,303 samples with a total of 9,936,568 defense systems (mean 8.5 systems detected per sample). 

## Software versions

DefenseFinder relies on 'models', consisting of HMMs for detecting hits then definitions/rules for calling systems as present. We used:

* DefenseFinder [v1.3.0](https://github.com/mdmparis/defense-finder/releases/tag/v1.3.0) (release link)
* CasFinder [v3.1.0](https://github.com/macsy-models/CasFinder/archive/refs/tags/3.1.0.tar.gz) (download link)
* defense-finder-models [v1.3.0](https://github.com/mdmparis/defense-finder-models/releases/download/1.3.0/defense-finder-models-v1.3.0.tar.gz) (download link)

With these versions, in principle 151 defense systems and 16 anti-defense systems can be detected. Newer defense systems that were added to DefenseFinder models after July 2024 will not be present in the results. For context, as of February 2026 the latest release of DefenseFinder is v2.0.1 and the latest models version (v2.0.2) contains 262 defense systems. 


## Workflow

The basic command for each sample was identical:

```
defense-finder run ${SAMPLE}.fa
```

We split this analysis between the three of us on three separate computing clusters, and each of us did it slightly differently. For each species, we downloaded all assemblies matching that species name from AllTheBacteria using `atb-downloader` from [Bactopia](https://github.com/bactopia/bactopia/tree/dev):

```
bactopia atb-downloader --query <SPECIES>
```

For an example slurm array job to run DefenseFinder on a single sample at a time, assuming you have a list of `samples.txt` that you have not already downloaded, see `array-job.sh`. For an example of how to download all samples for a species and then run DefenseFinder sequentially in a loop, see `sequential-job.sh`. Depending on the computing resources available to you, batching samples together in array jobs may make for better efficiency - bear this in mind for running large numbers of samples.

One of us (Liam) combined all our results together into a single tsv. See `checking_data.py` for some light checks made like checking for samples with/without defense systems against the overall species calls, and the construction of the status file. 

## Assembly quality

DefenseFinder was developed for complete genomes. Obviously this is not the case for AllTheBacteria assemblies! And our list of samples also includes samples assessed as low-quality after running the `assembly-stats` pipeline (those results are available on OSF [here](https://osf.io/h7wzy/files/7t9qd)).

We found that 653 samples did not have any defense systems detected, and we verified this with a rerun of DefenseFinder for these samples. The majority of these were low-quality (354/653, 54.2%) (HQ=F in OSF species calls), two orders of magnitude more than the low-quality proportion in samples where at least one system was detected (n=6909, 0.59%), highlighting that under-detection is likely a greater problem for more fragmented assemblies. However, we also noticed that low-quality samples sometimes had higher numbers of unique systems: the most defense systems observed in a high-quality sample was 33, but some low-quality samples had higher numbers up to 57. 

## Acknowledgements

Many thanks to the authors of DefenseFinder who made this analysis possible by making their tool openly available! We gratefully acknowledge and cite the publications this analysis rests on:

* "Systematic and quantitative view of the antiviral arsenal of prokaryotes" Nature Communications (2022) Tesson et al. [link](https://doi.org/10.1038/s41467-022-30269-9)
* "MacSyFinder v2: Improved modelling and search engine to identify molecular systems in genomes" Peer Community Journal (2023) Néron et al. [link](https://doi.org/10.24072/pcjournal.250)
* "CRISPRCasFinder, an update of CRISRFinder, includes a portable version, enhanced performance and integrates search for Cas proteins" Nucleic Acids Research (2018) Couvin et al. [link](https://doi.org/10.1093/nar/gky425)
