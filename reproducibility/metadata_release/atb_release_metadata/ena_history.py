"""Add retained ENA evidence and best-available assembly metadata without changing QC."""
import argparse
import ast
import csv
import gzip
import hashlib
import io
import json
import sqlite3
from pathlib import Path

from .io import open_text, read_header, sha256_file
from .sqlite_release import quote

FIELDS = ('sample_accession', 'fastq_ftp', 'fastq_md5', 'fastq_bytes', 'base_count',
          'read_count', 'instrument_platform', 'library_strategy', 'library_source', 'library_layout')
CORE = ('assembly', 'run', 'ena_latest', 'agc', 'assembly_stats', 'checkm2', 'sylph')
NEW = ('release', 'release_membership', 'ena_source', 'ena_observation',
       'assembly_run_provenance', 'assembly_ena')
OLD_SOURCES = (
    ('ena_661k', None, 'published_supplementary'),
    ('ena_20240625', '2024-06-25', 'complete_snapshot'),
    ('ena_20240801', '2024-08-01', 'complete_snapshot'),
    ('ena_202505_used', None, 'processing_capture'),
    ('ena_20250506', '2025-05-06', 'complete_snapshot'),
)


def iso_date(value):
    if not value:
        return None
    value = str(value)
    if len(value) == 8 and value.isdigit():
        return f'{value[:4]}-{value[4:6]}-{value[6:]}'
    return value


def fingerprint(con, table):
    digest = hashlib.sha256(); count = 0
    cursor = con.execute('SELECT * FROM ' + quote(table))
    while rows := cursor.fetchmany(5000):
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter='\t', quotechar=None, quoting=csv.QUOTE_NONE, lineterminator='\n')
        writer.writerows([['NA' if value is None else value for value in row] for row in rows])
        digest.update(buffer.getvalue().encode()); count += len(rows)
    return {'rows': count, 'content_sha256': digest.hexdigest()}


def association_match(sample, reported):
    return sample in (reported or '').replace(';', ',').split(',')


def export(con, table, directory):
    path = directory / f'atb.metadata.r1.{table}.tsv.gz'
    temporary = path.with_suffix(path.suffix + '.tmp')
    cursor = con.execute('SELECT * FROM ' + quote(table))
    count = 0
    with temporary.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding='utf-8', newline='') as handle:
                writer = csv.writer(handle, delimiter='\t', quotechar=None, quoting=csv.QUOTE_NONE, lineterminator='\n')
                writer.writerow([c[0] for c in cursor.description])
                for row in cursor:
                    writer.writerow(['NA' if v is None else v for v in row]); count += 1
    temporary.replace(path)
    print(f'Exported {table}: {count}', flush=True)
    return {'path': path.name, 'rows': count, 'sha256': sha256_file(path)}


def build(workspace, output_dir):
    root = workspace.resolve(); database = output_dir / 'atb.metadata.r1.sqlite'
    checkpoint = root / '202603_metadata_build_v2/ena_history.checkpoint.json'
    work = root / '202603_metadata_build_v2/ena_history.work.sqlite'
    if checkpoint.exists() or work.exists():
        raise ValueError('history checkpoint/work database already exists; inspect before restarting')
    con = sqlite3.connect(database)
    con.execute('PRAGMA cache_size=-131072')
    con.create_function('sample_matches', 2, association_match, deterministic=True)
    baseline = {table: fingerprint(con, table) for table in CORE}
    checkpoint.write_text(json.dumps({'baseline': baseline}, indent=2)+'\n')
    print('Frozen table fingerprints recorded', flush=True)
    def guard(action, table, column, db, trigger):
        if db == 'main' and table in CORE and action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    con.set_authorizer(guard)
    previous = root / '202505_files/atb.metadata.202505.sqlite'
    con.execute('ATTACH DATABASE ? AS history', (f'file:{previous}?mode=ro',))
    con.execute('ATTACH DATABASE ? AS staging', (str(work),))
    con.executescript('''
      CREATE TABLE release(release_id TEXT PRIMARY KEY,release_name TEXT NOT NULL,
        release_date TEXT,description TEXT NOT NULL);
      CREATE TABLE release_membership(release_id TEXT NOT NULL REFERENCES release,
        sample_accession TEXT NOT NULL REFERENCES assembly,PRIMARY KEY(release_id,sample_accession)) WITHOUT ROWID;
      INSERT INTO release VALUES('1','Release 1',NULL,'Complete standalone release through 202603; mixed AGC archives.');
      INSERT INTO release_membership SELECT '1',sample_accession FROM assembly WHERE in_release_1=1;
      CREATE TABLE ena_source(source_id TEXT PRIMARY KEY,description TEXT NOT NULL,
        source_kind TEXT NOT NULL,snapshot_date TEXT,date_basis TEXT NOT NULL,
        original_filename TEXT NOT NULL,sha256 TEXT NOT NULL,source_url TEXT,
        source_table TEXT,notes TEXT,columns_json TEXT NOT NULL);
      CREATE TABLE ena_observation(observation_id INTEGER PRIMARY KEY,source_id TEXT NOT NULL REFERENCES ena_source,
        run_accession TEXT NOT NULL,observed_date TEXT,
        sample_accession TEXT,fastq_ftp TEXT,fastq_md5 TEXT,fastq_bytes TEXT,base_count TEXT,read_count TEXT,
        instrument_platform TEXT,library_strategy TEXT,library_source TEXT,library_layout TEXT);
      CREATE TABLE staging.process_record(run_accession TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE assembly_run_provenance(provenance_id INTEGER PRIMARY KEY,
        assembly_sample_accession TEXT NOT NULL REFERENCES assembly(sample_accession),run_accession TEXT,
        observation_id INTEGER REFERENCES ena_observation,evidence_basis TEXT NOT NULL,notes TEXT,
        UNIQUE(assembly_sample_accession,run_accession));
    ''')
    specs = []
    old_sha = sha256_file(previous)
    for source_id, date, kind in OLD_SOURCES:
        columns = [r[1] for r in con.execute(f'PRAGMA history.table_info({source_id})')]
        specs.append(dict(id=source_id, date=date, kind=kind, columns=columns,
                          filename=previous.name, sha=old_sha, table='history.'+source_id))
    snapshot = root/'20260317_files/metadata.20260317.tsv.gz'
    specs.append(dict(id='ena_20260317',date='2026-03-17',kind='complete_snapshot',
                      columns=read_header(snapshot), filename=snapshot.name,sha=sha256_file(snapshot), table='main.ena_latest'))
    capture = root/'20260317_files/final.ena_meta.jsonl.xz'
    capture_columns = set()
    with open_text(capture) as handle:
        for line in handle:
            row = ast.literal_eval(line)
            capture_columns.update(row)
            con.execute('INSERT INTO staging.process_record VALUES (?,?)',
                        (row['run_accession'], json.dumps(row, sort_keys=True)))
    con.commit()
    specs.append(dict(id='ena_process_202603',date=None,kind='processing_capture',
                      columns=sorted(capture_columns),filename=capture.name,sha=sha256_file(capture),table=None))
    union = sorted(set().union(*(set(s['columns']) for s in specs)) - {'run_accession'})
    metadata_columns = ['assembly_sample_accession','run_accession','observation_id','source_id',
                        'observed_date','selection_basis','association_match','processing_read_identity_match','notes']
    if set(union) & set(metadata_columns):
        raise ValueError('ENA column conflicts with provenance column')
    for spec in specs:
        sid = spec['id']; columns = spec['columns']
        basis = 'Per-record obtained_date (metadata retrieval batch)' if sid=='ena_202505_used' else 'Snapshot retrieval date' if spec['date'] else 'Exact observation date unavailable'
        notes = 'Full source is retained separately. NULL denotes unavailable fields; no cross-source gap filling.'
        if spec['table'] and spec['table'].startswith('history.'):
            notes += ' Original snapshot is embedded in the archived 202505 SQLite table identified by source_table.'
        con.execute('INSERT INTO ena_source VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (sid,sid.replace('_',' '),spec['kind'],spec['date'],basis,spec['filename'],spec['sha'],None,
                     spec['table'].split('.')[-1] if spec['table'] else None,notes,json.dumps(columns)))
        if spec['table']:
            selected = ['run_accession'] + [c for c in FIELDS if c in columns] + (['obtained_date'] if 'obtained_date' in columns else [])
            where = " WHERE atb_present_in_current_snapshot='1'" if sid=='ena_20260317' else ''
            cursor = con.execute(f'SELECT {",".join(map(quote,selected))} FROM {spec["table"]}{where} ORDER BY run_accession')
            records = (dict(zip(selected, row)) for row in cursor)
        else:
            records = (json.loads(r[0]) for r in con.execute('SELECT payload FROM staging.process_record ORDER BY run_accession'))
        batch = []; count = 0
        for row in records:
            date = iso_date(row.get('obtained_date')) if sid=='ena_202505_used' else spec['date']
            batch.append((sid,row['run_accession'],date,*(row.get(c) for c in FIELDS)))
            if len(batch)==10000:
                con.executemany('INSERT INTO ena_observation VALUES(NULL,'+','.join('?' for _ in range(13))+')',batch)
                count += len(batch); batch.clear(); con.commit()
        con.executemany('INSERT INTO ena_observation VALUES(NULL,'+','.join('?' for _ in range(13))+')',batch)
        count += len(batch); con.commit(); spec['count']=count
        print(f'Observed {sid}: {count}',flush=True)
    con.executescript('''
      CREATE UNIQUE INDEX observation_source_run_date ON ena_observation(source_id,run_accession,coalesce(observed_date,''));
      CREATE INDEX observation_run ON ena_observation(run_accession);
    ''')
    for sample, runs in con.execute("SELECT sample_accession,run_accession FROM assembly WHERE asm_fasta_on_osf=1 OR assembly_seqkit_sum NOT IN ('NA','','seqkit.v0.1_LS_k0_56fc8fa222734a7d4b9ddedcca604bbf') ORDER BY sample_accession"):
        used = sorted(set(runs.replace(';',',').split(',')) - {'','NA','unknown'})
        for run in used or [None]:
            con.execute('INSERT INTO assembly_run_provenance VALUES(NULL,?,?,NULL,?,?)',
                        (sample,run,'unavailable','Processing-time ENA observation not established.' if run else 'No recoverable run accession.'))
    con.commit()
    con.executescript('''
      CREATE INDEX provenance_run ON assembly_run_provenance(run_accession);
      CREATE TABLE _chosen(provenance_id INTEGER PRIMARY KEY,observation_id INTEGER NOT NULL,score INTEGER NOT NULL);
    ''')
    primary = {'661k':'ena_661k','Incr_release.202408':'ena_20240801',
               'Incr_release.202505':'ena_202505_used','Incr_release.202603':'ena_process_202603'}
    # Processing evidence is distinct from best descriptive metadata.
    for dataset, sid in primary.items():
        candidates = con.execute('''SELECT p.provenance_id,min(o.observation_id),count(*)
          FROM assembly_run_provenance p JOIN assembly a ON a.sample_accession=p.assembly_sample_accession
          JOIN ena_observation o ON o.run_accession=p.run_accession AND o.source_id=?
          WHERE a.dataset=? AND sample_matches(p.assembly_sample_accession,o.sample_accession)
          GROUP BY p.provenance_id''',(sid,dataset)).fetchall()
        evidence = 'historical_inference' if dataset=='661k' else 'selection_snapshot' if dataset=='Incr_release.202408' else 'process_capture'
        for pid, oid, n in candidates:
            if n==1:
                con.execute('UPDATE assembly_run_provenance SET observation_id=?,evidence_basis=?,notes=? WHERE provenance_id=?',
                            (oid,evidence,'661k run choice inferred using published metadata and original rules.' if dataset=='661k' else 'Source tied to this processing batch; observation date is metadata retrieval date when available.',pid))
            else:
                con.execute('UPDATE assembly_run_provenance SET notes=? WHERE provenance_id=?',
                            ('Multiple processing observations; exact observation used cannot be established.',pid))
        con.commit()
    for rank,spec in enumerate(specs):
        sid=spec['id']; sparse=0 if 'country' in spec['columns'] else 100
        matching = 'sample_matches(p.assembly_sample_accession,o.sample_accession)'
        preferred = ' OR '.join('(a.dataset='+repr(dataset)+' AND o.source_id='+repr(source)+')' for dataset,source in primary.items())
        score = f'CASE WHEN {matching} THEN {sparse} + CASE WHEN ({preferred}) THEN 0 ELSE {20+rank} END ELSE {10000+len(specs)-rank} END'
        con.execute(f'''INSERT INTO _chosen
          SELECT p.provenance_id,o.observation_id,{score} FROM assembly_run_provenance p
          JOIN assembly a ON a.sample_accession=p.assembly_sample_accession
          JOIN ena_observation o ON o.run_accession=p.run_accession
          WHERE o.source_id=? ORDER BY o.observed_date,o.observation_id
          ON CONFLICT(provenance_id) DO UPDATE SET observation_id=excluded.observation_id,score=excluded.score
          WHERE excluded.score<=_chosen.score''',(sid,))
        con.commit(); print(f'Selected candidate metadata from {sid}',flush=True)
    con.execute('CREATE TABLE assembly_ena ('+
                'assembly_sample_accession TEXT NOT NULL REFERENCES assembly(sample_accession),run_accession TEXT NOT NULL,'+
                'observation_id INTEGER REFERENCES ena_observation,source_id TEXT REFERENCES ena_source,observed_date TEXT,'+
                'selection_basis TEXT NOT NULL,association_match INTEGER,processing_read_identity_match INTEGER,notes TEXT,'+
                ','.join(quote(c)+' TEXT' for c in union)+',PRIMARY KEY(assembly_sample_accession,run_accession))')
    con.execute('''INSERT INTO assembly_ena(assembly_sample_accession,run_accession,selection_basis,notes)
      SELECT p.assembly_sample_accession,p.run_accession,'unavailable','No retained ENA observation.'
      FROM assembly_run_provenance p LEFT JOIN _chosen c USING(provenance_id)
      WHERE c.provenance_id IS NULL AND p.run_accession IS NOT NULL''')
    for spec in specs:
        sid=spec['id']; fields=spec['columns']
        values=[]
        for field in union:
            if field not in fields: values.append('NULL')
            elif spec['table']: values.append('e.'+quote(field))
            else: values.append('json_extract(e.payload,?)')
        field_args=['$.'+f for f in union if f in fields] if not spec['table'] else []
        source = spec['table'] or 'staging.process_record'
        date_join = ' AND e.obtained_date=replace(o.observed_date,\'-\',\'\')' if sid=='ena_202505_used' else ''
        identity = "CASE WHEN used.observation_id IS NULL THEN NULL ELSE (o.fastq_md5=used.fastq_md5 AND o.fastq_bytes=used.fastq_bytes AND o.base_count=used.base_count) END"
        sql=f'''INSERT INTO assembly_ena SELECT p.assembly_sample_accession,p.run_accession,
          o.observation_id,o.source_id,o.observed_date,
          CASE WHEN NOT sample_matches(p.assembly_sample_accession,o.sample_accession) THEN 'latest_known_fallback'
               WHEN p.observation_id=o.observation_id THEN p.evidence_basis
               WHEN a.dataset IN ('661k','r0.2') THEN 'historical_inference' ELSE 'selection_snapshot_fallback' END,
          sample_matches(p.assembly_sample_accession,o.sample_accession),{identity},
          CASE WHEN NOT sample_matches(p.assembly_sample_accession,o.sample_accession)
            THEN 'Association conflict: descriptive metadata reports a different sample; do not interpret its attributes as verified assembly-sample metadata.'
            WHEN p.observation_id=o.observation_id THEN 'Selected intact source record; see processing evidence basis.'
            ELSE 'Best available intact descriptive record; not established as the record used for assembly. Check processing_read_identity_match.' END,
          {','.join(values)} FROM _chosen c JOIN assembly_run_provenance p USING(provenance_id)
          JOIN assembly a ON a.sample_accession=p.assembly_sample_accession
          JOIN ena_observation o ON o.observation_id=c.observation_id
          LEFT JOIN ena_observation used ON used.observation_id=p.observation_id
          JOIN {source} e ON e.run_accession=o.run_accession{date_join}
          WHERE o.source_id=?'''
        con.execute(sql,(*field_args,sid)); con.commit()
        print(f'Copied full descriptive records from {sid}',flush=True)
    con.executescript('''
      CREATE INDEX assembly_ena_run ON assembly_ena(run_accession);
      CREATE INDEX assembly_ena_tax_id ON assembly_ena(tax_id);
      CREATE INDEX assembly_ena_species ON assembly_ena(scientific_name);
      CREATE INDEX assembly_ena_country ON assembly_ena(country);
      DROP TABLE _chosen;
    ''')
    # No source record: retain an explicit row with NULL full metadata.
    def zero(query,label):
        n=con.execute(query).fetchone()[0]
        if n: raise ValueError(f'{label}: {n}')
    zero("SELECT count(*) FROM (SELECT sample_accession FROM assembly WHERE in_release_1=1 EXCEPT SELECT sample_accession FROM release_membership WHERE release_id='1')",'missing members')
    zero("SELECT count(*) FROM (SELECT sample_accession FROM release_membership WHERE release_id='1' EXCEPT SELECT sample_accession FROM assembly WHERE in_release_1=1)",'extra members')
    zero('SELECT count(*) FROM release_1 a LEFT JOIN assembly_run_provenance p ON a.sample_accession=p.assembly_sample_accession WHERE p.provenance_id IS NULL','uncovered assemblies')
    zero('SELECT count(*) FROM assembly_run_provenance p LEFT JOIN assembly_ena e ON e.assembly_sample_accession=p.assembly_sample_accession AND e.run_accession=p.run_accession WHERE p.run_accession IS NOT NULL AND e.assembly_sample_accession IS NULL','missing assembly/run metadata')
    if con.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('foreign key errors')
    if con.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]: raise ValueError('integrity errors')
    after={table:fingerprint(con,table) for table in CORE}
    if baseline!=after: raise ValueError('FROZEN TABLE CHANGED')
    counts={table:con.execute('SELECT count(*) FROM '+quote(table)).fetchone()[0] for table in NEW}
    choices=dict(con.execute('SELECT selection_basis,count(*) FROM assembly_ena GROUP BY selection_basis'))
    evidence=dict(con.execute('SELECT evidence_basis,count(*) FROM assembly_run_provenance GROUP BY evidence_basis'))
    print(json.dumps({'counts':counts,'selection':choices,'evidence':evidence}),flush=True)
    con.execute('DETACH DATABASE staging'); con.execute('DETACH DATABASE history')
    report_path=output_dir/'atb.metadata.r1.sqlite.report.json'
    report=json.loads(report_path.read_text())
    for table in NEW: report['exports'][table]=export(con,table,output_dir)
    report['history_extension']={'frozen_tables':baseline,'counts':counts,'selection_counts':choices,'processing_evidence_counts':evidence,
       'policy':'Descriptive records are intact source rows; processing evidence is independently linked. Original snapshots remain separate archives. Source URLs await verification.'}
    con.close()
    report['database']['sha256']=sha256_file(database)
    report.pop('compressed_database',None)
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    checkpoint.write_text(json.dumps({'status':'PASS','baseline':baseline,'after':after,'counts':counts},indent=2)+'\n')
    checkpoint.replace(root/'202603_metadata_build_v2/ena_history.validation.json')
    work.unlink()
    print('History extension and exports complete; recompress database and regenerate checksums.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    build(**vars(parser.parse_args()))
