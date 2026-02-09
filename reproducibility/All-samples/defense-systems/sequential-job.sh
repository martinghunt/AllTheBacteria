# Activate bactopia-dev environment: see https://github.com/bactopia/bactopia/tree/dev
conda activate bactopia-dev
# Download all samples from AllTheBacteria that match "species name"
bactopia atb-downloader -q "species name"
conda deactivate
# Gunzip the files
gunzip *.fa.gz
# Activate defense-finder environment
conda activate defense-finder-env
# Run on all downloaded samples with a loop
for i in *.fa; do defense-finder run $i ; done &

