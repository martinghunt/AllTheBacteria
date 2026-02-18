#!/usr/bin/bash -l
#SBATCH -A cluster_projectID
#SBATCH --time=01:00:00
#SBATCH -N 1 -c 28
#SBATCH -J ATB_btyper3

## Load modules
ml Nextflow/25.10.0

## Arguments
ATB_maindir=$1 # Parent dirctory for ATB B. cereus group specific analysis

# Run BTyper3 module (executed via Bactopia) in a loop for all the relevant sylph species
cat ${ATB_maindir}/Bacillus_A_count.tsv | grep -v "^sylph" | cut -f 1 | \
while read sylph_species; \
do \
bash ${ATB_maindir}/src/BaT_btyper3.sh ${sylph_species} ${ATB_maindir}; \
echo -e "[ATB-btyper3]: Finished BTyper3 analysis for sylph_species - ${sylph_species}"; \
done
