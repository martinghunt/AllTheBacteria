import pandas as pd
import os 
from rich import print
from rich.progress import track
import math


def run_ectyper(directory_fasta, output_dir, not_done_genomes, fasta_suffix='.fa.gz', MIN_PER_JOB = 100, MAX_JOBS = 500, submit=True):
    print(f"[bold blue]Setting up ECTyper on fasta files in {directory_fasta}[/bold blue]")
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    print(f"Will look for {len(not_done_genomes)} genomes that have not been processed.")
    # Search recursively for all fasta files with the given suffix in the directory and subdirectories
    fasta_files = []
    # make done_genomes a dictionary to look up faster
    not_done_genomes_dict = {os.path.basename(genome).split('.')[0]: genome for genome in not_done_genomes}
    for root, dirs, files in os.walk(directory_fasta):
        for file in files:
            if file.endswith(fasta_suffix):
                if os.path.basename(file).split('.')[0] in not_done_genomes_dict:
                    # Only add files that have not been processed
                    fasta_files.append(os.path.join(root, file))

    total_files = len(fasta_files)
    # If len of total_files is < len(not_done_genomes) - warn
    if total_files < len(not_done_genomes):
        print('Missing files. Warning.')
    
    # Calculate ideal chunk size
    chunk_size = max(MIN_PER_JOB, math.ceil(total_files / MAX_JOBS))

    print(f"[bold green]Found {len(fasta_files)} fasta files to process.[/bold green]")
    # Create chunks of fasta files
    chunks = [fasta_files[i:i + chunk_size] for i in range(0, len(fasta_files), chunk_size)]
    # Run ectyper for each chunk
    abs_path = os.path.abspath(output_dir)
    jobs_dir = os.path.join(abs_path, 'jobs')
    os.makedirs(jobs_dir, exist_ok=True)    
    # Clear existing jobs files
    for file in os.listdir(jobs_dir):
        if file.endswith('.sh'):
            os.remove(os.path.join(jobs_dir, file))
    logs_dir = os.path.join(abs_path, 'logs')    
    os.makedirs(logs_dir, exist_ok=True)
    # Clear existing logs files
    for file in os.listdir(logs_dir):
        if file.endswith('.out') or file.endswith('.err'):
            os.remove(os.path.join(logs_dir, file))    
    print(f"[bold blue]Processing {len(chunks)} chunks of fasta files with ECTYPER[/bold blue]")
    for idx, chunk in enumerate(track(chunks, description="Processing chunks with ECTYPER")):
        # Create a SLURM job script
        slurm_script = os.path.join(jobs_dir, f'slurm_ectyper_{idx}.sh')
        with open(slurm_script, 'w') as f:
            chunks_2 = [chunk[i:i + 20] for i in range(0, len(chunk), 20)]
            time_needed = len(chunks_2) * 6   # Assuming each chunk of 20 takes about 6 minutes
            # convert time_needed to HH:MM:SS format
            hours = time_needed // 60
            minutes = time_needed % 60
            time_str = f"{hours:02}:{minutes:02}:00"
            f.write("#!/bin/bash\n")
            f.write(f"#SBATCH --job-name=ectyper{idx}\n")
            f.write(f"#SBATCH --output={logs_dir}/ectyper_{idx}.out\n")
            f.write(f"#SBATCH --error={logs_dir}/ectyper_{idx}.err\n")
            f.write(f"#SBATCH --time={time_str}\n")
            f.write("#SBATCH --cpus-per-task=1\n")
            f.write("#SBATCH --mem=4G\n")
            f.write("\n")
            # Create chunks of 20 fasta files
            for idx2, chunkagain in enumerate(chunks_2):
                file_list = ' '.join(chunkagain)
                f.write(f"/users/aanensen/rva470/.pixi/bin/pixi run ectyper --verify --pathotype -i {file_list} -o {abs_path}/ectyper_results_{idx}_{idx2} \n")
        # Submit the SLURM job
        if submit:
            os.system(f"sbatch {slurm_script}")

def fetch_ectyper_result(file_path):

    ectyper_df = pd.read_csv(file_path, sep='\t', on_bad_lines='skip')
    # Rename Name to Sample
    ectyper_df = ectyper_df.rename(columns={'Name': 'Sample'})
    return ectyper_df

ECTYPER_COLUMNS = ['Species', 'SpeciesMashRatio', 'SpeciesMashDistance', 'SpeciesMashTopID', 'O-type', 'H-type', 'Serotype', 'QC', 'Evidence', 'GeneScores', 'AlleleKeys', 'GeneIdentities(%)', 'GeneCoverages(%)', 'GeneContigNames', 'GeneRanges', 'GeneLengths', 'DatabaseVer', 'Warnings', 'Pathotype', 'PathotypeCounts', 'PathotypeGenes', 'PathotypeGeneNames', 'PathotypeAccessions', 'PathotypeAlleleIDs', 'PathotypeIdentities(%)', 'PathotypeCoverages(%)', 'PathotypeGeneLengthRatios', 'PathotypeRuleIDs', 'PathotypeGeneCounts', 'PathoDBVer', 'StxSubtypes', 'StxAccessions', 'StxAlleleIDs', 'StxAlleleNames', 'StxIdentities(%)', 'StxCoverages(%)', 'StxLengths', 'StxContigNames', 'StxCoordinates']

def check_ectyper_results(output_dir, parquet_file):
    """
    Check the ECTYPER results in the output directory and update the main results file.
    """
    
    # Load existing parquet file
    df = pd.read_parquet(parquet_file)
    df = df.rename(columns={'sample': 'Sample'}) if 'sample' in df.columns else df
    # Drop duplicate columns 'Sample'
    df = df.loc[:, ~df.columns.duplicated()]
    # Create a new column if it doesn't exists ['Sample', 'Scheme', 'ST', 'Alleles']]
    # ST should not be 'nan' (string)

    for col in ECTYPER_COLUMNS:
        if col not in df.columns:
            df[col] = ""
            df[col] = df[col].astype(str)
    list_of_not_done = df[(df['Serotype'].isin(["", "nan", "NaN", "Nan"])) | (df['Serotype'].isna())]['Sample'].tolist()
    print(f"[bold green]Loaded existing parquet file: {parquet_file}[/bold green]")
    ectyper_results_folders = [f for f in os.listdir(output_dir) if f.startswith('ectyper_results_')]
    if not ectyper_results_folders:
        print(f"[bold red]No ECTYPER results folders found in {output_dir}[/bold red]")
        return list_of_not_done
    # Initialize a list to hold all results
    all_results = []
    read_files = [] 
    for ectyper_folder in track(ectyper_results_folders, description="Processing ECTYPER result folders"):
        file_path = os.path.join(output_dir, ectyper_folder, 'output.tsv')
        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            ectyper_df = fetch_ectyper_result(file_path)
            read_files.append(file_path)
            all_results.append(ectyper_df)
        else:
            print(f"[bold red]MLST results file not found: {file_path}[/bold red]")
            if os.path.exists(file_path):
                print(f"[bold red]File exists but is empty: {file_path}[/bold red]")
                read_files.append(file_path)
    # Concatenate all results into a single DataFrame
    if all_results:
        all_results_df = pd.concat(all_results, ignore_index=True)
        print(f"[bold green]Found {len(all_results_df)} ECTYPER results across {len(ectyper_results_folders)} files[/bold green]")
    else:
        print(f"[bold red]No ECTYPER results found in the files[/bold red]")
        return list_of_not_done
    # Now we have all the ECTYPER    results in a single DataFrame
    # We need to merge this with the existing DataFrame from the parquet file
    # Ensure the Sample column is in both DataFrames
    if 'Sample' not in df.columns:
        print(f"[bold red]Sample column not found in the existing DataFrame[/bold red]")
        return list_of_not_done
    # Merge the ECTYPER results with the existing DataFrame
    # Ensure both DataFrames have the same column name for merging
    # Fill in values from all_results_df if df is missing them. 
    df = df.merge(all_results_df, on='Sample', how='left', suffixes=('', '_ectyper'))

    # For each overlapping column, fill missing values with the _ectyper version
    for col in all_results_df.columns:
        if col != "Sample" and f"{col}_ectyper" in df.columns:
            mask = df[col].isna() | df[col].isin(["", "nan", "NaN", "Nan"])
            df.loc[mask, col] = df.loc[mask, f"{col}_ectyper"]
            df.drop(columns=[f"{col}_ectyper"], inplace=True)


    # Update the main results file with the new results 
    # Convert object columns that should be numeric to avoid PyArrow conversion errors
    numeric_columns = ['PathotypeCounts', 'PathotypeGeneCounts', 'SpeciesMashRatio', 'SpeciesMashDistance', 
                      'GeneLengths', 'StxLengths', 'PathotypeGeneLengthRatios']
    percentage_columns = ['GeneIdentities(%)', 'GeneCoverages(%)', 'PathotypeIdentities(%)', 
                         'PathotypeCoverages(%)', 'StxIdentities(%)', 'StxCoverages(%)']
    
    for col in numeric_columns:
        if col in df.columns:
            # Convert to numeric, replace non-numeric with 0
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype('int64')
    
    for col in percentage_columns:
        if col in df.columns:
            # Convert to numeric, replace non-numeric with 0.0 for percentages
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0).astype('float64')
    
    # Ensure all remaining object columns are strings to avoid PyArrow conversion issues
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].astype(str)
    
    df.to_parquet(parquet_file, index=False, engine="pyarrow")
    df.to_csv(parquet_file.replace('.parquet', '.csv'), index=False)
    print(f"[bold green]Updated ECTYPER results saved to {parquet_file}[/bold green]")
    list_of_not_done = df[(df['Serotype'].isin(["", "nan", "NaN", "Nan"])) | (df['Serotype'].isna())]['Sample'].tolist()
    # Write file for ATB 
    atb_file = os.path.join(output_dir, 'ectyper_atb_results.tsv')
    df[['Sample'] + ECTYPER_COLUMNS].to_csv(atb_file, sep='\t', index=False)
    # Create a status file. “PASS”, “FAIL”, “NOT DONE”,
    status_file = os.path.join(output_dir, 'ectyper_atb_status_results.tsv')
    # Define rules for status
    def get_status(row):
        if row['Serotype'] in ["nan", "NaN", "Nan"] or pd.isna(row['Serotype']):
            return "NOT DONE"
        elif row['Serotype'].lower() in ["-:-"]:  # adjust depending on how failures are marked
            return "FAIL"
        else:
            return "PASS"

    df['Status'] = df.apply(get_status, axis=1)
    # Save status file
    df[['Sample', 'Status']].to_csv(status_file, sep="\t", index=False)    
    return list_of_not_done

def merge_csv_genome_file(name, directory, output_dir):
    parquet_file = os.path.join(output_dir, name + "_merged_genome_file.parquet")
    if os.path.exists(parquet_file):
        return parquet_file
    filtered_file = None
    high_quality_file = None
    print('Creating merged dataframe')
    # Find filtered_out_genomes.csv
    for root, _, files in os.walk(directory):
        for filename in files:
            if filename.endswith("filtered_out_genomes.csv.xz"):
                filtered_file = os.path.join(root, filename)
                break
        if filtered_file:
            break

    # Find high_quality_genome.csv
    for root, _, files in os.walk(directory):
        for filename in files:
            if filename.endswith("high_quality_genomes.csv.xz"):
                high_quality_file = os.path.join(root, filename)
                break
        if high_quality_file:
            break

    # If neither file exists
    if not filtered_file and not high_quality_file:
        return None

    # Read CSVs into pandas
    dfs = []
    if filtered_file:
        dfs.append(pd.read_csv(filtered_file, compression='xz'))
    if high_quality_file:
        dfs.append(pd.read_csv(high_quality_file, compression='xz'))

    # Merge with union of columns, filling missing with NaN
    merged_df = pd.concat(dfs, ignore_index=True, sort=False)
    # Testing. Only use the first 100 genomes
    # merged_df = merged_df.head(100)
    # Write merged Parquet file
    os.makedirs(output_dir, exist_ok=True)
    merged_df.to_parquet(parquet_file, index=False)
    return parquet_file


def main(directory_fasta, output_dir, genome_table_dir, name):
    os.makedirs(output_dir, exist_ok=True)
    parq_file = merge_csv_genome_file(name, genome_table_dir, output_dir)
    if parq_file:
        not_done_genomes = check_ectyper_results(output_dir, parq_file)  # Check if results already exist
        if len(not_done_genomes) > 0:
            run_ectyper(directory_fasta, output_dir, not_done_genomes)
        else:
            print(f"[bold green]All genomes already processed for {name}[/bold green]")
                             


if __name__ == "__main__":   
    # directory_fasta = '/well/aanensen/shared/atb/ecoli/'
    directory_fasta = '/well/aanensen/users/rva470/bigscript/my_assemblies'
    output_dir = 'ecoli_ectyper_results'
    genome_table_dir = '../qualibact/docs/Escherichia/Escherichia_coli/'
    name = "ecoli"
    main(directory_fasta, output_dir, genome_table_dir, name)

