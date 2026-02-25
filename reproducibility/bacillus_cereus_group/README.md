# *Bacillus cereus* group spp. - Typing

## Standardized taxonomic classification, sequence typing, detection of virulence and Bt toxin-encoding genes


All [sylph](https://github.com/bluenote-1577/sylph)-identified *Bacillus cereus* group genomes (genus classification - "Bacillus_A") from [AllTheBacteria](https://allthebacteria.readthedocs.io/en/latest/) ``v0.2`` and ``incremental release 2024-08`` **(n=4,867 genome assemblies)** were typed using [BTyper3](https://github.com/lmc297/BTyper3), which conducts standardized *B. cereus* group specific taxonomic classification and sequence typing using a variety of methods, as well as detection of virulence and Bt toxin-encoding genes.

[BTyper3](https://github.com/lmc297/BTyper3) v3.4.0 was run as a module within [Bactopia](https://github.com/bactopia/bactopia) v3.2.0, using [Nextflow](https://github.com/nextflow-io/nextflow) v25.10.0 on the HPC2N cluster (Umeå University).

## Workflow
### Gather assemblies

```bash
# file_list.all.latest.tsv - ATB file list (v0.2+inc-rel-2024-08) with sample info and paths to fasta files
# example file structure
sample  species_sylph   species_miniphy filename_in_tar_xz      tar_xz  tar_xz_url      tar_xz_md5      tar_xz_size_MB
SAMN12335635    Achromobacter xylosoxidans      achromobacter_xylosoxidans      atb.assembly.r0.2.batch.1/SAMN12335635.fa       atb.assembly.r0.2.batch.1.tar.xz   https://osf.io/download/667142936b6c8e33f404cce7/       65973bf642ddd13bfd4ffffc37b72efb        32.67

# List of Bacillus cereus group samples (sylph genus classfication - Bacillus_A) 
cat ${ATB_maindir_all_genomes}/file_list.all.latest.tsv | awk -v FS='\t' -v OFS='\t' 'NR==1;NR>1{if($2~/^Bacillus_A /) print }' >> ${ATB_maindir}/Bacillus_A_file_list.latest.tsv

# Extract fasta files from associated archive into a common assemblies folder (organized per sylph species inside genus "Bacillus_A")
cat ${ATB_maindir}/Bacillus_A_file_list.latest.tsv | grep -v "^sample" | cut -f 2 | sed 's/ /_/' | sort | uniq | while read sylph_species; do mkdir -p  ${ATB_maindir}/assemblies/${sylph_species};done
cat ${ATB_maindir}/Bacillus_A_file_list.latest.tsv | grep -v "^sample" | cut -f 2,4-5 | sed 's/Bacillus_A /Bacillus_A_/' | while read sylph_species file_path file_archive; do tar -xf ${ATB_maindir_all_genomes}/0.2/${file_archive} -C ${ATB_maindir}/assemblies/${sylph_species} ${file_path} --strip-components=1;done

# Compress the assemblies
find ${ATB_maindir}/assemblies/ -type f -name "*.fa" | while read file; do pigz ${file};done
```
### BTyper3 analysis

#### Scripts
* ATB_btyper3.sh - Slurm script to run BTyper3 module (BaT_btyper3.sh) in a loop for all the relevant sylph species
* BaT_btyper3.sh - BTyper3 module executed via Bactopia

``` bash
# Create a bactopia directory structure for all the assemblies
# Symlink the assemblies into their respective sylph species subfolders in results

ls -d ${ATB_maindir}/assemblies/Bacillus_A_* | rev | cut -d "/" -f 1 | rev | while read sylph_species; do \
apptainer exec ${CONTAINERS}/bactopia_3.2.0--3f0d29b1421fe836.sif \
bactopia atb-formatter \
--path ${ATB_maindir}/assemblies/${sylph_species}/ \
--recursive \
--bactopia-dir ${ATB_maindir}/results/${sylph_species}/ \
--extension ".fa.gz"
--publish-mode symlink; \
done 2> /dev/null

# Sanity Check
echo -e "sylph_species\t#_Assemblies" > ${ATB_maindir}/Bacillus_A_count.tsv
ls -d ${ATB_maindir}/results/Bacillus_A_* | rev | cut -d "/" -f 1 | rev | while read sylph_species; do \
assemblies=`ls ${ATB_maindir}/results/${sylph_species}/*/main/assembler/*.fna.gz | wc -l`; \
echo -e "${sylph_species}\t${assemblies}";done >> ${ATB_maindir}/Bacillus_A_count.tsv

# Run BTyper3 (as part of Bactopia Tools workflow)
sbatch ${ATB_maindir}/src/ATB_btyper3.sh ${ATB_maindir}
```

### Aggregate results 

``` bash
# Collect BTyper3 results for all B. cereus group assemblies (alongwith sylph species information)
# Aggregate and merge results in one file, sort by sylph species

# Header
cat ${ATB_maindir}/results/Bacillus_A_albus/SAMD00053578/tools/btyper3/SAMD00053578_final_results.txt | head -1 | cut -f 2- | sed 's/prefix/Sample/' | awk -v FS='\t' -v OFS='\t' '{print "species_sylph",$0}' > ${ATB_maindir}/ATB_Bacillus_A_BTyper3_merged.tsv
# Sample row(s)
cat ${ATB_maindir}/Bacillus_A_file_list.latest.tsv | grep -v "^sample" | cut -f 1,2 | sed 's/Bacillus_A /Bacillus_A_/' | \
while read sample sylph_species; do \
btyper3_results=`cat ${ATB_maindir}/results/${sylph_species}/${sample}/tools/btyper3/${sample}_final_results.txt | cut -f 2- | grep "^${sample}"`; \
echo -e "${sylph_species}\t${btyper3_results}";done >> ${ATB_maindir}/ATB_Bacillus_A_BTyper3_merged.tsv

# Sorted tsv (based on sylph species name)
cat ${ATB_maindir}/ATB_Bacillus_A_BTyper3_merged.tsv | head -1 > ATB_0.2+inc-rel-2024-08_Bacillus-cereus-group_BTyper3-v3.4.0_merged.tsv
cat ${ATB_maindir}/ATB_Bacillus_A_BTyper3_merged.tsv | grep -v "^species_sylph" | sort -k1 >> ATB_0.2+inc-rel-2024-08_Bacillus-cereus-group_BTyper3-v3.4.0_merged.tsv
```

For any questions regarding *Bacillus cereus* group spp. typing/analysis, please contact [Laura Carroll](mailto:laura.carroll@umu.se).
