#!/bin/bash

#SBATCH --job-name=defensefinder
#SBATCH --nodes=1
#SBATCH --cpus-per-task=4
#SBATCH --time=00:20:00 # in practice most run <10min
#SBATCH --mem=200M # in practice many run <100M
#SBATCH --array=1

# Setting params to make sure DefenseFinder sees availability of threads (unclear if this really matters)
export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export MKL_NUM_THREADS=$SLURM_CPUS_PER_TASK
export OPENBLAS_NUM_THREADS=$SLURM_CPUS_PER_TASK

# Get name of sample from sample list
name=$(sed -n "${SLURM_ARRAY_TASK_ID}p" samples.txt)

# Download the assembly
wget https://allthebacteria-assemblies.s3.eu-west-2.amazonaws.com/"$name".fa.gz
gunzip "$name".fa.gz

# Initiate conda environment with DefenseFinder
. ~/initMamba.sh
conda activate defensefinder-1.3.0

# Temporary directory (defensefinder will complain if you set off simultaneous array jobs with same output dir)
mkdir -p tmp_${SLURM_ARRAY_TASK_ID}
# Run Defense-Finder
defense-finder run SAMN21988166.fa --workers ${SLURM_CPUS_PER_TASK} -o tmp_${SLURM_ARRAY_TASK_ID}
