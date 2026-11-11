"""Apply reviewed NCBI alias evidence to descriptive ENA metadata only."""
import argparse
import csv
import json
import sqlite3
import urllib.parse
from pathlib import Path

from .ena_history import CORE, association_match, export
from .io import sha256_file
from .ncbi_alias_audit import parse_records
from .sqlite_release import quote

PINNED = set(CORE) | {'assembly_run_provenance', 'release', 'release_membership', 'ena_observation', 'ena_source', 'ena_661k_sample'}


def guard(action, table, column, db, trigger):
    if db=='main' and table in PINNED and action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_DROP_TABLE):
        return sqlite3.SQLITE_DENY
    if action==sqlite3.SQLITE_ALTER_TABLE and column in PINNED:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def apply(output_dir, audit_dir, resume_exports=False):
    with (audit_dir/'ncbi_alias_audit.tsv').open() as handle:
        rows=[row for row in csv.DictReader(handle,delimiter='\t',quoting=csv.QUOTE_NONE)
              if row['audit_status']=='ALIAS_PUBLIC_SAMPLE_MATCH' and row['candidate_count']=='1']
    if len(rows)!=2:
        raise ValueError('expected the two reviewed alias candidates; review other results explicitly')
    evidence={}
    for row in rows:
        path=audit_dir/'cache'/row['evidence_file']
        meta=json.loads(path.with_name(path.stem+'.request.json').read_text())
        if sha256_file(path)!=meta['response_sha256']:
            raise ValueError('alias evidence checksum mismatch')
        matches=[r for r in parse_records(path.read_bytes()) if r['primary_run']==row['primary_run_accession']
                 and row['run_accession'] in r['secondary_runs'] and row['sample_accession'] in r['biosamples']
                 and r['is_public']=='true']
        if len(matches)!=1: raise ValueError('alias lacks explicit matching public RUN/BioSample evidence')
        evidence[row['evidence_file']]=(path,meta)
    public_names={name:f'atb.metadata.r1.run_alias.evidence.{i:02d}.xml' for i,name in enumerate(sorted(evidence),1)}
    for name,(path,meta) in evidence.items():
        target=output_dir/public_names[name]
        if target.exists() and sha256_file(target)!=meta['response_sha256']:
            raise ValueError('refusing to replace different public evidence')
        target.write_bytes(path.read_bytes())
    database=output_dir/'atb.metadata.r1.sqlite'
    con=sqlite3.connect(database)
    con.row_factory=sqlite3.Row
    con.set_authorizer(guard)
    columns=[r[1] for r in con.execute('PRAGMA table_info(assembly_ena)')]
    if not resume_exports:
        if 'ena_run_accession' in columns or con.execute("SELECT 1 FROM sqlite_master WHERE name='run_alias'").fetchone():
            raise ValueError('alias schema already exists; validate before resuming')
        with con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('ALTER TABLE assembly_ena ADD COLUMN ena_run_accession TEXT')
            con.execute('''CREATE TABLE run_alias(
              assembly_sample_accession TEXT NOT NULL REFERENCES assembly(sample_accession),
              recorded_run_accession TEXT NOT NULL,primary_run_accession TEXT NOT NULL,
              metadata_observation_id INTEGER NOT NULL REFERENCES ena_observation(observation_id),
              evidence_provider TEXT NOT NULL,evidence_retrieved_at TEXT NOT NULL,
              evidence_url TEXT NOT NULL,evidence_filename TEXT NOT NULL,evidence_sha256 TEXT NOT NULL,
              notes TEXT NOT NULL,PRIMARY KEY(assembly_sample_accession,recorded_run_accession,primary_run_accession))''')
            con.execute('UPDATE assembly_ena SET ena_run_accession=run_accession WHERE observation_id IS NOT NULL')
            raw_columns=set(json.loads(con.execute("SELECT columns_json FROM ena_source WHERE source_id='ena_20260317'").fetchone()[0]))
            meta_columns={'assembly_sample_accession','run_accession','observation_id','source_id','observed_date',
                          'selection_basis','association_match','processing_read_identity_match','notes','ena_run_accession'}
            for row in rows:
                sample,old,new=row['sample_accession'],row['run_accession'],row['primary_run_accession']
                existing=con.execute('SELECT observation_id FROM assembly_ena WHERE assembly_sample_accession=? AND run_accession=?',(sample,old)).fetchone()
                if existing is None or existing[0] is not None:
                    raise ValueError('expected an existing unavailable metadata row')
                original=con.execute('SELECT observation_id FROM assembly_run_provenance WHERE assembly_sample_accession=? AND run_accession=?',(sample,old)).fetchone()
                if original is None or original[0] is not None:
                    raise ValueError('processing evidence changed; review before applying fallback')
                obs=con.execute("SELECT * FROM ena_observation WHERE source_id='ena_20260317' AND run_accession=?",(new,)).fetchone()
                latest=con.execute('SELECT * FROM ena_latest WHERE run_accession=?',(new,)).fetchone()
                if obs is None or latest is None or latest['atb_present_in_current_snapshot']!='1' or not association_match(sample,latest['sample_accession']):
                    raise ValueError('retained ENA primary accession/sample could not be verified')
                path,proof=evidence[row['evidence_file']]
                public=public_names[row['evidence_file']]
                url=proof['url']+'?'+urllib.parse.urlencode(proof['parameters'])
                note=f'Best-available ENA metadata via NCBI SRA secondary accession {old} -> {new}, observed {proof["retrieved_at"]}. Original assembly input preserved; processing-time metadata and FASTQ byte identity remain unestablished.'
                values={c:latest[c] if c in raw_columns else None for c in columns if c not in meta_columns}
                values.update(observation_id=obs['observation_id'],source_id=obs['source_id'],observed_date=obs['observed_date'],
                              selection_basis='accession_alias_fallback',association_match=1,processing_read_identity_match=None,
                              notes=note,ena_run_accession=new)
                con.execute('UPDATE assembly_ena SET '+','.join(quote(c)+'=?' for c in values)+
                            ' WHERE assembly_sample_accession=? AND run_accession=?',(*values.values(),sample,old))
                con.execute('INSERT INTO run_alias VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (sample,old,new,obs['observation_id'],'NCBI SRA',proof['retrieved_at'],url,public,proof['response_sha256'],note))
            con.execute('INSERT INTO release_provenance VALUES(?,?)',('ncbi_alias_enrichment_20261110',json.dumps({
                'aliases':[{k:r[k] for k in ('sample_accession','run_accession','primary_run_accession')} for r in rows],
                'policy':'Original assembly inputs, processing-time provenance, membership, QC and AGC are unchanged. Descriptive ENA rows use retained primary-accession observations, qualified as accession_alias_fallback.',
                'ena_run_accession':'ENA source record primary accession; run_accession remains the recorded assembly input.'},sort_keys=True)))
    if con.execute('SELECT count(*) FROM run_alias').fetchone()[0]!=2:
        raise ValueError('alias count mismatch')
    for row in rows:
        selected=con.execute('SELECT * FROM assembly_ena WHERE assembly_sample_accession=? AND run_accession=?',
                             (row['sample_accession'],row['run_accession'])).fetchone()
        if selected is None or selected['ena_run_accession']!=row['primary_run_accession'] or selected['selection_basis']!='accession_alias_fallback':
            raise ValueError('alias metadata verification failed')
    bad=con.execute('''SELECT count(*) FROM assembly_ena e LEFT JOIN ena_observation o USING(observation_id)
       LEFT JOIN run_alias r ON r.assembly_sample_accession=e.assembly_sample_accession
        AND r.recorded_run_accession=e.run_accession AND r.primary_run_accession=e.ena_run_accession
       WHERE e.ena_run_accession IS NOT o.run_accession
          OR (e.observation_id IS NOT NULL AND (e.sample_accession IS NOT o.sample_accession
              OR e.fastq_md5 IS NOT o.fastq_md5 OR e.fastq_bytes IS NOT o.fastq_bytes
              OR e.base_count IS NOT o.base_count OR e.read_count IS NOT o.read_count))
          OR (e.ena_run_accession!=e.run_accession AND r.recorded_run_accession IS NULL)''').fetchone()[0]
    if bad: raise ValueError(f'alias-aware source consistency failures: {bad}')
    # Check only changed B-trees; the underlying scientific tables are write-protected.
    for table in ('assembly_ena','run_alias','release_provenance'):
        if [tuple(r) for r in con.execute('PRAGMA integrity_check('+table+')')]!=[('ok',)]:
            raise ValueError('integrity failure: '+table)
    if con.execute('PRAGMA foreign_key_check(run_alias)').fetchall(): raise ValueError('alias foreign key failure')
    report_path=output_dir/'atb.metadata.r1.sqlite.report.json'
    report=json.loads(report_path.read_text())
    for table in ('assembly_ena','run_alias','release_provenance'):
        report['exports'][table]=export(con,table,output_dir)
    report['alias_enrichment']={'count':2,'evidence_provider':'NCBI SRA',
                              'policy':'ENA supplies metadata; NCBI supplies explicit accession alias evidence. Frozen tables and processing provenance are write-protected.',
                              'protected_tables':sorted(PINNED)}
    report['history_extension']['counts']['run_alias']=2
    report['history_extension']['selection_counts']=dict(con.execute('SELECT selection_basis,count(*) FROM assembly_ena GROUP BY selection_basis'))
    report.setdefault('supporting_files',{})
    for name,(_,proof) in evidence.items():
        report['supporting_files'][public_names[name]]={'path':public_names[name],'sha256':proof['response_sha256']}
    con.close()
    report['database']['sha256']=sha256_file(database)
    report.pop('compressed_database',None)
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print('Alias enrichment complete; regenerate joined export, compressed SQLite, and checksums.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--audit-dir',type=Path,required=True)
    parser.add_argument('--resume-exports',action='store_true')
    apply(**vars(parser.parse_args()))
