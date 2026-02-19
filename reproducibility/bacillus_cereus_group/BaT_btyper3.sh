#!/usr/bin/bash -l

## Arguments
sylph_species=$1
ATB_maindir=$2
logs_path=${ATB_maindir}/logs/

## Logfile
mkdir -p ${logs_path}/BaT-btyper3/
exec > ${logs_path}/BaT-btyper3/${sylph_species}.txt

## Nextflow Launch Dir
mkdir -p ${logs_path}/nf-run/${sylph_species}/BaT-btyper3/
cd ${logs_path}/nf-run/${sylph_species}/BaT-btyper3/

## Run BTyper3 on Samples

echo -e "[BaT-btyper3]: Starting Analysis\n\
Sample Info: ${sylph_species}\n"

nextflow run bactopia/bactopia \
--wf btyper3 \
--bt_overlap 0.7 \
--bt_opts "--ani_geneflow True" \
--bactopia ${ATB_maindir}/results/${sylph_species}/ \
--max_cpus 1 \
-profile singularity

## Copy log files
cp .nextflow.log ${logs_path}/BaT-btyper3/${sylph_species}.nextflow.log

## Cleanup
rm -r ${logs_path}/nf-run/${sylph_species}/

echo -e "[BaT-btyper3]: Done. \n"
