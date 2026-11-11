"""Simplify the published SQLite schema without changing assembly selections."""
import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from .ena_history import CORE, export, fingerprint
from .io import sha256_file
from .sqlite_release import quote

LATEST = ('sample_accession', 'fastq_md5', 'fastq_bytes', 'base_count', 'fastq_ftp', 'read_count')
RETIRED = ('ena_latest', 'ena_661k_sample')


def history_columns(con):
    columns = [r[1] for r in con.execute('PRAGMA table_info(ena_latest)')]
    missing = set(('run_accession',) + LATEST) - set(columns)
    if missing:
        raise ValueError('Missing latest metadata columns: '+str(sorted(missing)))
    return ['run_accession'] + ['latest_'+c for c in LATEST] + [c for c in columns if c.startswith('atb_')]


def transform(con, separate_files):
    """Apply the reviewed schema changes atomically; guard other table contents."""
    columns = history_columns(con)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    protected = tables - set(RETIRED) - {'release_provenance'}

    def guard(action, table, column, db, trigger):
        if db == 'main' and table in protected and action in (
                sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_DROP_TABLE):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_ALTER_TABLE and column in protected and column != 'assembly_ena':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    con.set_authorizer(guard)
    try:
        with con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('ALTER TABLE assembly_ena RENAME TO assembly_ena_metadata')
            protected.add('assembly_ena_metadata')
            con.execute('CREATE TABLE ena_run_history (' + ','.join(
                quote(c)+(' TEXT PRIMARY KEY NOT NULL' if c == 'run_accession' else ' TEXT') for c in columns)+')')
            source = ['run_accession'] + list(LATEST) + [c for c in columns if c.startswith('atb_')]
            con.execute('INSERT INTO ena_run_history SELECT '+','.join(map(quote, source))+' FROM ena_latest')
            mismatch = con.execute('SELECT count(*) FROM ena_latest l LEFT JOIN ena_run_history h USING(run_accession) WHERE '+
                ' OR '.join('h.'+quote(dest)+' IS NOT l.'+quote(src) for dest,src in zip(columns,source))).fetchone()[0]
            if mismatch or con.execute('SELECT count(*) FROM ena_latest').fetchone()[0] != con.execute('SELECT count(*) FROM ena_run_history').fetchone()[0]:
                raise ValueError('Compact history differs from original latest values')
            for table in RETIRED:
                con.execute('DROP TABLE '+quote(table))
            con.execute('INSERT INTO release_provenance VALUES(?,?)', ('publication_schema', json.dumps({
                'renamed_tables': {'assembly_ena':'assembly_ena_metadata'},
                'separate_files': separate_files,
                'ena_run_history_columns': columns,
                'policy': 'Assembly metadata, inputs, processing provenance, membership, QC, AGC and retained observations are unchanged. Full latest ENA and original File9 metadata remain separate downloads.',
            }, sort_keys=True)))
    finally:
        con.set_authorizer(None)
    return columns


def build(output_dir, work_dir):
    database = output_dir/'atb.metadata.r1.sqlite'
    report_path = output_dir/'atb.metadata.r1.sqlite.report.json'
    report = json.loads(report_path.read_text())
    readme_template = Path(__file__).parent/'release_1_README.md'
    if not readme_template.is_file():
        raise ValueError('Missing release README template')
    if report.get('publication_schema'):
        raise ValueError('Publication schema already applied; inspect before restarting')
    work_dir.mkdir(parents=True, exist_ok=True)
    working = work_dir/'publication_schema.work.sqlite'
    compact = work_dir/'publication_schema.compact.sqlite'
    if working.exists() or compact.exists():
        raise ValueError('Work databases already exist; inspect before restarting')
    if sha256_file(database) != report['database']['sha256']:
        raise ValueError('Database differs from its export report')
    separate = {table: report['exports'][table] for table in RETIRED}
    # Validate the full metadata downloads before removing their database tables.
    for meta in separate.values():
        if sha256_file(output_dir/Path(meta['path']).name) != meta['sha256']:
            raise ValueError('Separate metadata export checksum mismatch')
    archive = work_dir/'previous_publication'
    archive.mkdir(exist_ok=False)
    for filename in ('atb.metadata.r1.sqlite.xz','atb.metadata.r1.sqlite.report.json','SHA256SUMS','MD5SUMS','README.md'):
        if (output_dir/filename).exists():
            shutil.copy2(output_dir/filename, archive/filename)
    source = sqlite3.connect(f'file:{database.resolve()}?mode=ro', uri=True)
    con = sqlite3.connect(working)
    print('Copying canonical database to isolated work database.', flush=True)
    source.backup(con)
    source.close()
    con.execute('PRAGMA secure_delete=OFF')
    before = {table: con.execute('SELECT count(*) FROM '+quote(table)).fetchone()[0]
              for table in ('assembly_ena','assembly_run_provenance','release_membership','ena_source','ena_observation','run_alias')}
    selected_before = fingerprint(con,'assembly_ena')
    print('Applying table rename and compact latest-run history.', flush=True)
    columns = transform(con,separate)
    print('Compacting database; full metadata exports remain separate.', flush=True)
    con.execute('VACUUM INTO ?', (str(compact.resolve()),))
    con.close()
    con = sqlite3.connect(compact)
    print('Checking compact database integrity.', flush=True)
    integrity = con.execute('PRAGMA integrity_check').fetchall()
    if integrity != [('ok',)]:
        raise ValueError('Integrity check failed: '+str(integrity[:10]))
    if con.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Foreign key check failed')
    if fingerprint(con,'assembly_ena_metadata') != selected_before:
        raise ValueError('Selected assembly ENA metadata changed')
    frozen = report['history_extension']['frozen_tables']
    for table in CORE:
        if table in RETIRED:
            continue
        if fingerprint(con,table) != frozen[table]:
            raise ValueError('Scientific table changed: '+table)
        print('Unchanged scientific table: '+table, flush=True)
    counts = {}
    for table,count in before.items():
        target = 'assembly_ena_metadata' if table == 'assembly_ena' else table
        counts[target] = con.execute('SELECT count(*) FROM '+quote(target)).fetchone()[0]
        if counts[target] != count:
            raise ValueError('Protected row count changed: '+target)
    counts['ena_run_history'] = con.execute('SELECT count(*) FROM ena_run_history').fetchone()[0]
    # The renamed table has identical columns, row order and content. Its existing
    # dump remains byte-for-byte valid; the filename now follows the table name.
    selected = report['exports'].pop('assembly_ena')
    old_path = output_dir/Path(selected['path']).name
    if sha256_file(old_path) != selected['sha256']:
        raise ValueError('Selected metadata export changed')
    new_path = output_dir/'atb.metadata.r1.assembly_ena_metadata.tsv.gz'
    if new_path.exists():
        raise ValueError('Renamed metadata export already exists')
    selected['path'] = new_path.name
    report['exports']['assembly_ena_metadata'] = selected
    for table in RETIRED:
        report['exports'].pop(table)
    report['separate_files'] = separate
    report['exports']['ena_run_history'] = export(con,'ena_run_history',output_dir)
    report['exports']['release_provenance'] = export(con,'release_provenance',output_dir)
    con.close()
    # Retain superseded convenience export outside the publication directory.
    previous_join = report['exports'].pop('release_1_assembly_ena',None)
    if previous_join:
        (output_dir/Path(previous_join['path']).name).replace(archive/Path(previous_join['path']).name)
    old_path.replace(new_path)
    compact.replace(database)
    report['publication_schema'] = {'selected_metadata_table':'assembly_ena_metadata',
        'separate_tables':list(RETIRED),'history_columns':columns,'validated_counts':counts,
        'selected_metadata_content':selected_before,'protected_scientific_tables': [t for t in CORE if t not in RETIRED],
        'integrity_check':'ok','foreign_key_check':'ok'}
    extension = report['history_extension']
    extension['pre_publication_schema_counts'] = extension['counts'].copy()
    extension['counts'].pop('assembly_ena')
    extension['counts'].pop('ena_661k_sample')
    extension['counts'].update(counts)
    extension['external_table_fingerprints'] = {'ena_latest':extension['frozen_tables'].pop('ena_latest')}
    report['database']['sha256'] = sha256_file(database)
    report.pop('compressed_database',None)
    report['distribution'] = {'include_uncompressed_database':False,
        'notes':'Uncompressed SQLite is retained locally for inspection. Publish its compressed copy, exports, separate metadata downloads, evidence, report, README and checksum manifests.'}
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    shutil.copyfile(readme_template,output_dir/'README.md')
    working.unlink()
    print('Publication schema complete. Regenerate joined export, compressed database and checksums.',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    build(**vars(parser.parse_args()))
