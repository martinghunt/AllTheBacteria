"""Read-only, cached NCBI SRA audit of assembly runs without retained ENA metadata."""
import argparse
import csv
import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
ACCESSION = re.compile(r'(?:SRR|ERR|DRR)[0-9]+\Z')


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Client:
    def __init__(self, cache):
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)
        self.last = 0.0
        self.network_requests = 0
        self.cache_hits = 0

    def request(self, endpoint, params, validator, suffix):
        params = {'db': 'sra', 'tool': 'atb_metadata_alias_audit', **params}
        body = urllib.parse.urlencode(params).encode()
        key = hashlib.sha256(endpoint.encode()+b'\n'+body).hexdigest()
        path = self.cache / (key+suffix)
        if path.exists():
            payload = path.read_bytes()
            result = validator(payload)
            self.cache_hits += 1
            return result, path.name
        error = None
        for attempt in range(4):
            time.sleep(max(0, 1.05-(time.monotonic()-self.last)))
            self.last = time.monotonic()
            self.network_requests += 1
            try:
                request = urllib.request.Request(BASE+endpoint, data=body,
                           headers={'User-Agent': 'AllTheBacteria-metadata-audit/1.0'})
                with urllib.request.urlopen(request, timeout=45) as response:
                    payload = response.read()
                result = validator(payload)
                temporary = path.with_suffix(path.suffix+'.tmp')
                temporary.write_bytes(payload)
                temporary.replace(path)
                (self.cache/(key+'.request.json')).write_text(json.dumps({
                    'url': BASE+endpoint, 'parameters': params, 'retrieved_at': timestamp(),
                    'response_filename': path.name,
                    'response_sha256': hashlib.sha256(payload).hexdigest(),
                }, indent=2, sort_keys=True)+'\n')
                return result, path.name
            except (urllib.error.URLError, OSError, ValueError, ET.ParseError) as exc:
                error = f'{type(exc).__name__}: {exc}'
                retry_after = exc.headers.get('Retry-After', '') if isinstance(exc, urllib.error.HTTPError) else ''
                if attempt<3:
                    time.sleep(max(2**(attempt+1), int(retry_after) if retry_after.isdigit() else 0))
        raise RuntimeError(error)


def search_result(payload):
    result = json.loads(payload)
    if 'error' in result:
        raise ValueError(str(result['error']))
    result = result['esearchresult']
    if 'error' in result:
        raise ValueError(str(result['error']))
    if int(result['count']) != len(result['idlist']):
        raise ValueError('SRA search was truncated; no conclusions made for this batch')
    return result


def parse_records(payload):
    root = ET.fromstring(payload)
    for element in root.iter():
        element.tag = element.tag.split('}')[-1]
    if root.tag!='EXPERIMENT_PACKAGE_SET':
        raise ValueError('Unexpected SRA XML response: '+root.tag)
    records = []
    for package in root.findall('EXPERIMENT_PACKAGE'):
        experiment = package.find('EXPERIMENT')
        experiment_id = experiment.get('accession', '') if experiment is not None else ''
        sample = package.find('SAMPLE')
        organism = sample.findtext('SAMPLE_NAME/SCIENTIFIC_NAME', '') if sample is not None else ''
        attributes = defaultdict(set)
        if sample is not None:
            for attribute in sample.findall('SAMPLE_ATTRIBUTES/SAMPLE_ATTRIBUTE'):
                attributes[attribute.findtext('TAG', '')].add(attribute.findtext('VALUE', ''))
        for run in package.findall('RUN_SET/RUN'):
            primary = run.get('accession') or run.findtext('IDENTIFIERS/PRIMARY_ID', '')
            secondary = sorted({element.text.strip() for element in run.findall('IDENTIFIERS/SECONDARY_ID')
                                if element.text and ACCESSION.fullmatch(element.text.strip())})
            if not ACCESSION.fullmatch(primary):
                continue
            samples = {e.text.strip() for e in run.findall('Pool/Member/IDENTIFIERS/EXTERNAL_ID')
                       if e.get('namespace')=='BioSample' and e.text}
            if not samples and sample is not None:
                samples = {e.text.strip() for e in sample.findall('IDENTIFIERS/EXTERNAL_ID')
                           if e.get('namespace')=='BioSample' and e.text}
            records.append({
                'primary_run': primary, 'secondary_runs': secondary, 'biosamples': sorted(samples),
                'experiment_accession': experiment_id, 'scientific_name': organism,
                'is_public': run.get('is_public', ''), 'published': run.get('published', ''),
                'total_spots': run.get('total_spots', ''), 'total_bases': run.get('total_bases', ''),
                'country': sorted(attributes.get('geo_loc_name', set()) | attributes.get('country', set())),
                'collection_date': sorted(attributes.get('collection_date', set())),
                'host': sorted(attributes.get('host', set())),
            })
    return records


def write_tsv(path, columns, rows):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter='\t', lineterminator='\n',
                                quoting=csv.QUOTE_NONE, quotechar=None)
        writer.writeheader()
        writer.writerows(rows)


def audit(database, output_dir, batch_size=50):
    if not 1 <= batch_size <= 100:
        raise ValueError('batch size must be 1..100')
    output_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f'file:{database.resolve()}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    metadata_table = 'assembly_ena_metadata' if con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assembly_ena_metadata'").fetchone() else 'assembly_ena'
    inputs = [dict(row) for row in con.execute('''SELECT e.assembly_sample_accession AS sample_accession,
       e.run_accession,a.dataset,a.in_release_1 FROM '''+metadata_table+''' e JOIN assembly a
       ON a.sample_accession=e.assembly_sample_accession WHERE e.observation_id IS NULL
       ORDER BY e.run_accession,e.assembly_sample_accession''')]
    unknown = [dict(row) for row in con.execute('''SELECT p.assembly_sample_accession AS sample_accession,
       a.dataset,a.in_release_1 FROM assembly_run_provenance p JOIN assembly a
       ON a.sample_accession=p.assembly_sample_accession WHERE p.run_accession IS NULL
       ORDER BY p.assembly_sample_accession''')]
    con.close()
    accessions = sorted({row['run_accession'] for row in inputs})
    if not all(ACCESSION.fullmatch(a) for a in accessions):
        raise ValueError('invalid input run accession')
    input_columns = ('sample_accession', 'run_accession', 'dataset', 'in_release_1')
    manifest = {'database_filename': database.name, 'inputs': inputs, 'unknown_run_samples': unknown}
    input_path = output_dir/'inputs.json'
    if input_path.exists() and json.loads(input_path.read_text()) != manifest:
        raise ValueError('audit inputs differ from existing directory; use a new audit directory')
    input_path.write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
    write_tsv(output_dir/'inputs.tsv', input_columns, inputs)
    write_tsv(output_dir/'unknown_run_samples.tsv', ('sample_accession','dataset','in_release_1'), unknown)
    client = Client(output_dir/'cache')
    batches = []; errors = []
    for offset in range(0, len(accessions), batch_size):
        members = accessions[offset:offset+batch_size]
        batch = {'accessions': members, 'uids': []}
        try:
            result, evidence = client.request('esearch.fcgi', {
                'term': ' OR '.join(members), 'retmode': 'json', 'retmax': '10000',
            }, search_result, '.json')
            batch.update(uids=result['idlist'], evidence=evidence)
        except RuntimeError as exc:
            batch['error'] = str(exc)
            errors.append({'stage':'search', 'accessions':members, 'error':str(exc)})
        batches.append(batch)
        print(f'Search batch {len(batches)}: {len(members)} accessions, {len(batch["uids"])} SRA experiments'+(' ERROR' if 'error' in batch else ''), flush=True)
    uids = sorted({uid for batch in batches for uid in batch['uids']})
    records = []; failed_uids = set()
    for offset in range(0, len(uids), batch_size):
        members = uids[offset:offset+batch_size]
        try:
            found, evidence = client.request('efetch.fcgi', {'id':','.join(members), 'retmode':'xml'}, parse_records, '.xml')
            if not found:
                raise RuntimeError('SRA fetch returned no run records')
            records.extend({**row, 'evidence_file':evidence} for row in found)
            print(f'Fetch batch {offset//batch_size+1}: {len(found)} run records', flush=True)
        except RuntimeError as exc:
            failed_uids.update(members)
            errors.append({'stage':'fetch', 'uids':members, 'error':str(exc)})
    # Match only explicit RUN primary/secondary identifiers, never sample or description text.
    matches = defaultdict(dict)
    for record in records:
        for run in set([record['primary_run'], *record['secondary_runs']]) & set(accessions):
            key = (record['primary_run'], record['experiment_accession'], tuple(record['biosamples']))
            matches[run][key] = record
    batch_for = {accession:batch for batch in batches for accession in batch['accessions']}
    columns = (*input_columns, 'audit_status', 'candidate_count', 'primary_run_accession',
               'secondary_run_accessions', 'reported_biosamples', 'sample_match', 'is_public',
               'experiment_accession', 'scientific_name', 'total_spots', 'total_bases',
               'published', 'country', 'collection_date', 'host', 'evidence_file', 'notes')
    rows = []; counts = Counter(); released_counts = Counter()
    for item in inputs:
        run = item['run_accession']; candidates = list(matches[run].values()); batch = batch_for[run]
        if not candidates:
            status = 'REQUEST_ERROR' if 'error' in batch or failed_uids.intersection(batch['uids']) else 'NO_PUBLIC_RUN_MATCH_FOUND'
            row = dict.fromkeys(columns, '')
            row.update(item, audit_status=status, candidate_count=0,
                       evidence_file=batch.get('evidence', ''),
                       notes='No explicit primary/secondary run match found; this does not establish suppression or withdrawal.' if status!='REQUEST_ERROR' else 'A request failed; do not interpret this as a missing record.')
            rows.append(row)
        else:
            status = 'MULTIPLE_EXPLICIT_MATCHES' if len(candidates)>1 else ''
            for candidate in candidates:
                sample_match = 'unknown' if not candidate['biosamples'] else 'yes' if item['sample_accession'] in candidate['biosamples'] else 'no'
                if status == 'MULTIPLE_EXPLICIT_MATCHES':
                    candidate_status = status
                elif candidate['is_public'] != 'true':
                    candidate_status = 'EXPLICIT_NONPUBLIC_MATCH' if candidate['is_public']=='false' else 'EXPLICIT_MATCH_PUBLIC_STATUS_UNKNOWN'
                else:
                    candidate_status = ('PRIMARY' if run==candidate['primary_run'] else 'ALIAS')+'_PUBLIC_SAMPLE_'+{'yes':'MATCH','no':'MISMATCH','unknown':'UNVERIFIED'}[sample_match]
                row = dict.fromkeys(columns, '')
                row.update(item, audit_status=candidate_status, candidate_count=len(candidates),
                           primary_run_accession=candidate['primary_run'],
                           secondary_run_accessions=';'.join(candidate['secondary_runs']),
                           reported_biosamples=';'.join(candidate['biosamples']), sample_match=sample_match,
                           **{key:candidate[key] for key in ('is_public','experiment_accession','scientific_name','total_spots','total_bases','published','evidence_file')},
                           **{key:json.dumps(candidate[key],ensure_ascii=False) for key in ('country','collection_date','host')},
                           notes='Current NCBI metadata; explicit accession relationship does not prove FASTQ byte identity or historical sample identity. No release data changed.')
                rows.append(row)
                status = candidate_status
        counts[status] += 1
        if item['in_release_1']==1:
            released_counts[status] += 1
    write_tsv(output_dir/'ncbi_alias_audit.tsv', columns, rows)
    (output_dir/'search_batches.json').write_text(json.dumps(batches,indent=2,sort_keys=True)+'\n')
    summary = {'completed_at': timestamp(), 'input_pairs':len(inputs), 'unique_run_accessions':len(accessions),
               'unknown_run_samples_not_queried':len(unknown), 'sra_experiments_found':len(uids),
               'status_counts':dict(counts), 'release_1_status_counts':dict(released_counts),
               'network_requests':client.network_requests, 'cache_hits':client.cache_hits, 'errors':errors,
               'policy':'Audit only. Explicit primary/secondary RUN identifiers are required. Sample matching is separately reported. No index hit is not a suppression determination.'}
    summary['input_manifest_sha256'] = hashlib.sha256(input_path.read_bytes()).hexdigest()
    summary['audit_script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    publication_report = database.with_name('atb.metadata.r1.sqlite.report.json')
    if publication_report.exists():
        declared = json.loads(publication_report.read_text()).get('database', {})
        if Path(declared.get('path','')).name == database.name:
            summary['input_database'] = {'filename': database.name,
                'sha256_from_publication_report': declared.get('sha256'),
                'hash_basis': 'Published build report; database opened read-only, full-file hash not recomputed by audit.'}
    (output_dir/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    print(json.dumps(summary,indent=2), flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--batch-size',type=int,default=50)
    audit(**vars(parser.parse_args()))
