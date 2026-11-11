"""Apply ENA submission outcomes to a frozen release assembly table."""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

from .io import iter_tsv_dicts, open_tsv_writer, read_header, sha256_file


def apply(assembly: Path, status: Path, output: Path, report: Path):
    if output.exists() or report.exists():
        raise ValueError('refusing to overwrite outputs')
    outcomes = {}
    accessions = set()
    counts = Counter()
    for _, row in iter_tsv_dicts(status):
        sample, state, accession = row['Sample'], row['Status'], row['Accession']
        if sample in outcomes or not state:
            raise ValueError(f'duplicate sample or missing status: {sample}')
        if state == 'success':
            if not re.fullmatch(r'ERZ[0-9]+', accession) or accession in accessions:
                raise ValueError(f'invalid or duplicate accession: {accession}')
            accessions.add(accession)
        elif accession not in {'', 'None', 'NA'}:
            raise ValueError(f'failed submission has accession: {sample}')
        outcomes[sample] = row
        counts[state] += 1
    selected = set()
    for _, row in iter_tsv_dicts(assembly):
        if row['dataset'] == 'Incr_release.202603' and row['in_release_1'] == '1':
            sample = row['sample_accession']
            if sample in selected:
                raise ValueError(f'duplicate assembly sample: {sample}')
            selected.add(sample)
    if selected != set(outcomes):
        raise ValueError(f'submission/release sets differ: {len(selected-set(outcomes))} missing, {len(set(outcomes)-selected)} extra')
    header = read_header(assembly)
    with open_tsv_writer(output) as writer:
        writer.writerow(header)
        for _, row in iter_tsv_dicts(assembly):
            if row['sample_accession'] in outcomes:
                outcome = outcomes[row['sample_accession']]
                if outcome['Status'] == 'success':
                    row['assembly_accession'] = outcome['Accession']
                else:
                    row['assembly_accession'] = 'NA'
                    filters = set(row['asm_pipe_filter'].split(',')) - {'PASS', ''}
                    filters.add('ENA_ASM_SUBMIT_ERR')
                    row['asm_pipe_filter'] = ','.join(sorted(filters))
                    note = 'ENA assembly submission error: ' + outcome['Status']
                    row['comments'] = note if row['comments'] in {'', 'None', 'NA'} else row['comments'] + '; ' + note
            writer.writerow([row[column] for column in header])
    result = {'status': 'PASS', 'release_rows': len(selected), 'successful_submissions': counts['success'],
              'failed_submissions': len(selected)-counts['success'], 'submission_status_counts': dict(counts),
              'inputs': {str(p): sha256_file(p) for p in (assembly, status)},
              'output': {'path': str(output), 'sha256': sha256_file(output)},
              'policy': 'ENA submission failures do not change frozen release membership or HQ filters.'}
    report.write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('assembly', 'status', 'output', 'report'):
        parser.add_argument('--'+name, required=True, type=Path)
    result = apply(**vars(parser.parse_args()))
    print(json.dumps({key: result[key] for key in ('release_rows', 'successful_submissions', 'failed_submissions')}))
