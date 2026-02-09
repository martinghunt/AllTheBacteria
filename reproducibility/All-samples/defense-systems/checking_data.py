import pandas as pd
import re
import numpy as np


species_calls = pd.read_csv("~/Downloads/species_calls.tsv", sep="\t")

defense_systems = pd.read_csv("/Users/Liam/Downloads/combined-ATB-defense-finder/new/combined_all_data_6_feb.tsv", sep="\t")
# Add a sample column
defense_systems["Sample"] = defense_systems["sys_id"].str.replace(r"\..*$", "", regex=True)
cols = ["Sample"] + [c for c in defense_systems.columns if c != "Sample"]
defense_systems = defense_systems[cols]

samples_with_systems = set(defense_systems["Sample"])

def samples_for_species(species_name):
	'''gets all samples for a given species'''
	return list(species_calls[species_calls['Species']==species_name]['Sample'])


# List of species 
species_names = [x.strip() for x in open("/Users/Liam/Downloads/combined-ATB-defense-finder/new/species-names-formatted.txt", "r").readlines()]
samples_without_systems = set()
combined_species_samples = set()
for species in species_names:
	# For each species, we fetch all the sample calls
	species_samples = set(samples_for_species(species))
	combined_species_samples = combined_species_samples.union(species_samples)
	# We look at those that apparently don't have any defense systems
	species_samples_without_systems = species_samples - samples_with_systems
	samples_without_systems = samples_without_systems.union(species_samples_without_systems)

# Write these to a file so we can rerun them to verify
with open("/Users/Liam/Downloads/combined-ATB-defense-finder/new/rerun-samples.txt", "w") as f:
	for sample in samples_without_systems:
		f.write("%s\n" % sample)

# Write the combined data output to a single file
defense_systems.to_csv("/Users/Liam/Downloads/combined-ATB-defense-finder/new/combined_all_data_6_feb_for_upload.tsv", sep="\t", index=False)

# Assess the low/high-quality for samples with/without defense systems detected
species_calls[species_calls["Sample"].isin(samples_with_systems)]["HQ"].value_counts()
species_calls[species_calls["Sample"].isin(samples_without_systems)]["HQ"].value_counts()

# Make status file for our analysis
species_calls["Status"] = np.where(
    species_calls["Sample"].isin(combined_species_samples),
    "PASS",
    "NOT DONE"
)
cols = ["Sample", "Status"]
species_calls[cols].to_csv("/Users/Liam/Downloads/combined-ATB-defense-finder/new/defense_finder_status_20260206.tsv", sep='\t', index=False)


## Assembly statistics
# Make a summary table
system_counts = defense_systems.groupby("Sample")["subtype"].nunique() 
# assembly stats
assembly_stats = pd.read_csv("~/Downloads/assembly-stats.tsv", sep="\t")

combined = assembly_stats.merge(
	summary,
	left_index=True,
	right_on="Sample",
	how="inner")
# Add species, quality information
combined_2 = combined.merge(
	species_calls,
	left_index=True,
	right_on="Sample",
	how="inner"
	)
