import pandas as pd
import os 
from rich import print
from rich.progress import track
import math


def run_mlst(directory_fasta, output_dir, not_done_genomes, fasta_suffix='.fa.gz', MIN_PER_JOB = 100, MAX_JOBS = 500, submit=True):
    print(f"[bold blue]Setting up MLST on fasta files in {directory_fasta}[/bold blue]")
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
    # Run mlst for each chunk
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
    print(f"[bold blue]Processing {len(chunks)} chunks of fasta files with MLST[/bold blue]")
    for idx, chunk in enumerate(track(chunks, description="Processing chunks with MLST")):
        # Create a SLURM job script
        slurm_script = os.path.join(jobs_dir, f'slurm_mlst_{idx}.sh')
        with open(slurm_script, 'w') as f:
            chunks_2 = [chunk[i:i + 20] for i in range(0, len(chunk), 20)]
            time_needed = len(chunks_2)   # Assuming each chunk of 20 takes about 1 minute
            # convert time_needed to HH:MM:SS format
            hours = time_needed // 60
            minutes = time_needed % 60
            time_str = f"{hours:02}:{minutes:02}:00"
            f.write("#!/bin/bash\n")
            f.write(f"#SBATCH --job-name=mlst_{idx}\n")
            f.write(f"#SBATCH --output={logs_dir}/mlst_{idx}.out\n")
            f.write(f"#SBATCH --error={logs_dir}/mlst_{idx}.err\n")
            f.write(f"#SBATCH --time={time_str}\n")
            f.write("#SBATCH --cpus-per-task=1\n")
            f.write("#SBATCH --mem=4G\n")
            f.write("\n")
            # Create chunks of 20 fasta files
            for idx2, chunkagain in enumerate(chunks_2):
                file_list = ' '.join(chunkagain)
                # f.write(f"singularity run -B /well /well/aanensen/users/rva470/bigscript/mlst.sif mlst {file_list} > {abs_path}/mlst_results_{idx}_{idx2}.tsv \n")
                f.write(f"/users/aanensen/rva470/.pixi/bin/pixi run mlst {file_list} > {abs_path}/mlst_results_{idx}_{idx2}.tsv \n")
                # Need to use the mlst with updated databse run through pixi 
        # Submit the SLURM job
        if submit:
            os.system(f"sbatch {slurm_script}")

def fetch_mlst_result(file_path, mlst_file):
    try:
        # Read with error handling for variable column counts
        mlst_df = pd.read_csv(file_path, sep='\t', header=None, on_bad_lines='skip')
    except pd.errors.ParserError:
        # If that fails, try reading line by line
        print(f"[bold yellow]Parsing {mlst_file} line by line due to variable columns[/bold yellow]")
        rows = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():  # Skip empty lines
                    parts = line.strip().split('\t')
                    rows.append(parts)
        if not rows:
            raise ValueError(f"No valid data found in {mlst_file}")
        # Create DataFrame with maximum number of columns found
        max_cols = max(len(row) for row in rows)
        # Pad shorter rows with None
        padded_rows = [row + [None] * (max_cols - len(row)) for row in rows]
        mlst_df = pd.DataFrame(padded_rows)
    
    # Column 0 is filename, Column 1 is scheme, Column 2 is ST, Column 3+ is alleles
    # Handle alleles - join columns 3+ if they exist, otherwise empty string
    mlst_df['Alleles'] = mlst_df.iloc[:, 3:].apply(
            lambda x: ','.join(x.astype(str)) if not x.isna().all() else '', 
            axis=1)
    # Clean up alleles - remove empty strings and trailing commas
    mlst_df['Alleles'] = mlst_df['Alleles'].str.replace(r'^,+|,+$', '', regex=True)
    mlst_df['Sample'] = mlst_df.iloc[:, 0].apply(lambda x: os.path.basename(x).replace('.fa.gz', ''))
    mlst_df['ST'] = mlst_df.iloc[:, 2].astype(str)
    mlst_df['Scheme'] = mlst_df.iloc[:, 1].astype(str)
    # Select only the columns we need
    mlst_df = mlst_df[['Sample', 'Scheme', 'ST', 'Alleles']]
    return mlst_df

def check_mlst_results(output_dir, parquet_file):
    """
    Check the MLST results in the output directory and update the main results file.
    """
    
    # Load existing parquet file
    df = pd.read_parquet(parquet_file)
    df = df.rename(columns={'sample': 'Sample'}) if 'sample' in df.columns else df
    # Drop duplicate columns 'Sample'
    df = df.loc[:, ~df.columns.duplicated()]
    # Create a new column if it doesn't exists ['Sample', 'Scheme', 'ST', 'Alleles']]
    # ST should not be 'nan' (string)

    for col in ['Sample', 'Scheme', 'ST', 'Alleles']:
        if col not in df.columns:
            df[col] = ""
            df[col] = df[col].astype(str)
    list_of_not_done = df[(df['ST'] == "") | (df['Scheme'] == "")]['Sample'].tolist()
    print(f"[bold green]Loaded existing parquet file: {parquet_file}[/bold green]")
    # Then check against all the mlst_results.tsv files in the output directory
    # files will be name mlst_results_0_0.tsv etc.
    # Rows look like this:
    # /well/aanensen/shared/atb/ecoli/SAMEA114/SAMEA114771/SAMEA114771264.fa.gz	ecoli_achtman_4	744	adk(10)	fumC(11)	gyrB(135)	icd(8)	mdh(8)	purA(8)	recA(2)
    # Pull all the mlst results files from the output directory
    mlst_results_files = [f for f in os.listdir(output_dir) if f.startswith('mlst_results_') and f.endswith('.tsv')]
    if not mlst_results_files:
        print(f"[bold red]No MLST results files found in {output_dir}[/bold red]")
        return list_of_not_done
    # Initialize a list to hold all results
    all_results = []
    read_files = [] 
    for mlst_file in track(mlst_results_files, description="Processing MLST result files"):
        file_path = os.path.join(output_dir, mlst_file)
        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            mlst_df = fetch_mlst_result(file_path, mlst_file)
            read_files.append(file_path)
            all_results.append(mlst_df)
        else:
            print(f"[bold red]MLST results file not found: {file_path}[/bold red]")
            if os.path.exists(file_path):
                print(f"[bold red]File exists but is empty: {file_path}[/bold red]")
                read_files.append(file_path)
    # Concatenate all results into a single DataFrame
    if all_results:
        all_results_df = pd.concat(all_results, ignore_index=True)
        print(f"[bold green]Found {len(all_results_df)} MLST results across {len(mlst_results_files)} files[/bold green]")
    else:
        print(f"[bold red]No MLST results found in the files[/bold red]")
        return list_of_not_done
    # Now we have all the MLST results in a single DataFrame
    # We need to merge this with the existing DataFrame from the parquet file
    # Ensure the Sample column is in both DataFrames
    if 'Sample' not in df.columns:
        print(f"[bold red]Sample column not found in the existing DataFrame[/bold red]")
        return list_of_not_done
    # Merge the MLST results with the existing DataFrame
    # Ensure both DataFrames have the same column name for merging
    # Fill in values from all_results_df if df is missing them. 
    df = df.merge(all_results_df, on='Sample', how='left', suffixes=('', '_mlst'))

    # For each overlapping column, fill missing values with the _mlst version
    for col in all_results_df.columns:
        if col != "Sample" and f"{col}_mlst" in df.columns:
            mask = df[col].isna() | df[col].isin(["", "nan", "NaN", "Nan"])
            df.loc[mask, col] = df.loc[mask, f"{col}_mlst"]
            df.drop(columns=[f"{col}_mlst"], inplace=True)
    # Delete read files 
    # for read_file in read_files:
    #     os.remove(read_file)
    # give a list of those that didnt have a result. 
    missing_results = df[(df['ST'].isin(["", "nan", "NaN", "Nan"])) | (df['Scheme'].isin(["", "nan", "NaN", "Nan"]))]
    species = os.path.basename(output_dir).split('_')[0]
    if not missing_results.empty:
        # Write to file 
        missing_file = os.path.join(output_dir, f'{species}_missing_mlst_results.tsv')
        missing_results.to_csv(missing_file, sep='\t', index=False)
        print(f"[bold yellow]Missing MLST results saved to {missing_file}[/bold yellow]")
    else:
        print(f"[bold green]All samples have MLST results[/bold green]")
    # Create a list of totally failed genomes (Scheme == '-'). Write the rows to csv

    totally_failed_genomes = df[df['Scheme'] == '-']
    if not totally_failed_genomes.empty:
        # Write to file
        failed_file = os.path.join(output_dir, f'{species}_totally_failed_genomes.tsv')
        totally_failed_genomes.to_csv(failed_file, sep='\t', index=False)
        print(f"[bold yellow]Totally failed genomes saved to {failed_file}[/bold yellow]")

    # Update the main results file with the new results 
    df['ST'] = df['ST'].fillna("").astype(str)
    df.to_parquet(parquet_file, index=False)
    df.to_csv(parquet_file.replace('.parquet', '.csv'), index=False)
    print(f"[bold green]Updated MLST results saved to {parquet_file}[/bold green]")
    list_of_not_done = df[(df['ST'] == "") | (df['Scheme'] == "")]['Sample'].tolist()
    # Return list of already found MLST results but the list of IDs
    # Write file for ATB 
    # Sample Scheme ST Alleles 
    atb_file = os.path.join(output_dir, f'{species}_atb_mlst_results.tsv')
    df[['Sample', 'Scheme', 'ST', 'Alleles']].to_csv(atb_file, sep='\t', index=False)
    # Create a status file. “PASS”, “FAIL”, “NOT DONE”, 
    status_file = os.path.join(output_dir, f'{species}_atb_status_mlst_results.tsv')
    # Define rules for status
    def get_status(row):
        if row['ST'] == "" or row['Scheme'] == "":
            return "NOT DONE"
        elif row['Scheme'].lower() in ["-"]:  # adjust depending on how failures are marked
            return "FAIL"
        else:
            return "PASS"

    df['Status'] = df.apply(get_status, axis=1)
    # Save status file
    df[['Sample', 'Scheme', 'ST', 'Status']].to_csv(status_file, sep="\t", index=False)    
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
        not_done_genomes = check_mlst_results(output_dir, parq_file)  # Check if results already exist
        if len(not_done_genomes) > 0:
            run_mlst(directory_fasta, output_dir, not_done_genomes)
        else:
            print(f"[bold green]All genomes already processed for {name}[/bold green]")
                             


if __name__ == "__main__":
    # L mono    
    directory_fasta = '/well/aanensen/shared/atb/lmono/'
    output_dir = 'lmono_mlst_results_db'
    genome_table_dir = '../qualibact/docs/Listeria/Listeria_monocytogenes/'
    name = "lmono"
    main(directory_fasta, output_dir, genome_table_dir, name)

