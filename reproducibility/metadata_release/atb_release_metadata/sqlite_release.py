"""Build and export the canonical Release 1 SQLite metadata database."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import sqlite3
from pathlib import Path

from .io import iter_tsv_dicts, read_header, sha256_file


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def load(con, table, path, primary, integer=()):
    header = read_header(path)
    columns = [quote(c) + (' INTEGER' if c in integer else ' TEXT') +
               (' PRIMARY KEY NOT NULL' if c == primary else ' NOT NULL') for c in header]
    con.execute(f'CREATE TABLE {quote(table)} ({",".join(columns)})')
    insert(con, table, path, header)


def insert(con, table, path, columns, rename=None):
    rename = rename or {}
    sql = f'INSERT INTO {quote(table)} ({",".join(map(quote, columns))}) VALUES ({",".join("?" for _ in columns)})'
    batch = []
    for _, row in iter_tsv_dicts(path):
        row = {rename.get(k, k): v for k, v in row.items()}
        if set(row) != set(columns):
            raise ValueError(f'{path}: column mismatch for {table}')
        batch.append(tuple(row[c] for c in columns))
        if len(batch) == 10000:
            con.executemany(sql, batch); batch.clear()
    con.executemany(sql, batch)
    con.commit()
    print(f'Loaded {table}: {path}', flush=True)


def require(con, query, expected, label):
    actual = con.execute(query).fetchone()[0]
    if actual != expected:
        raise ValueError(f'{label}: expected {expected}, found {actual}')


def build(workspace, output_dir, finish_existing=False):
    root = workspace.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    database = output_dir / 'atb.metadata.r1.sqlite'
    if database.exists() and not finish_existing:
        raise ValueError(f'refusing to overwrite {database}')
    inputs = {
        'previous_database': root/'202505_files/atb.metadata.202505.sqlite',
        'assembly': root/'202603_metadata_build_v2/atb.metadata.r1.assembly.tsv.xz',
        'run': root/'202603_metadata_build_v2/atb.metadata.202603.run.tsv.xz',
        'ena_latest': root/'202603_metadata_build_v2/atb.metadata.202603.ena_latest.tsv.xz',
        'agc': root/'20260317_files/agc/atb.batches.r1.metadata.tsv.gz',
        'assembly_stats': root/'20260317_files/assembly-stats.tsv.gz',
        'checkm2': root/'20260317_files.SAMN50311804_fix/checkm2.20260317.tsv.xz',
        'sylph': root/'20260317_files/final.sylph.tsv.gz',
    }
    con = sqlite3.connect(database)
    con.execute('PRAGMA journal_mode=DELETE')
    con.execute('PRAGMA synchronous=FULL')
    con.execute('PRAGMA temp_store=FILE')
    con.execute('PRAGMA cache_size=-131072')
    try:
        if not finish_existing:
            load(con, 'assembly', inputs['assembly'], 'sample_accession', {'asm_fasta_on_osf', 'in_release_1'})
            load(con, 'run', inputs['run'], 'run_accession', {'pass', 'meta_pass_atb', 'meta_pass_661k', 'in_661k', 'in_ena_20240625', 'in_ena_20240801', 'in_ena_20250506', 'in_ena_20260317'})
            load(con, 'ena_latest', inputs['ena_latest'], 'run_accession')
            load(con, 'agc', inputs['agc'], 'agc_filename', {'genome_count', 'size_bytes'})
            previous = sqlite3.connect(f'file:{inputs["previous_database"]}?mode=ro', uri=True)
            con.execute('ATTACH DATABASE ? AS previous', (str(inputs['previous_database']),))
            for table in ('assembly_stats', 'checkm2', 'sylph'):
                schema = previous.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name=?', (table,)).fetchone()[0]
                con.execute(schema)
                con.execute(f'INSERT INTO {table} SELECT * FROM previous.{table}')
                con.commit()
                columns = [row[1] for row in con.execute(f'PRAGMA table_info({table})')]
                rename = {'sample': 'sample_accession'} if table == 'assembly_stats' else {'Name': 'sample_accession'} if table == 'checkm2' else {'Sample': 'sample_accession', 'Run': 'run_accession', 'ANI_5-95_percentile': 'ANI_5_95_percentile', 'Lambda_5-95_percentile': 'Lambda_5_95_percentile'}
                insert(con, table, inputs[table], columns, rename)
            previous.close()
            con.execute('DETACH DATABASE previous')
        con.executescript('''
          CREATE INDEX IF NOT EXISTS assembly_dataset ON assembly(dataset);
          CREATE INDEX IF NOT EXISTS assembly_release ON assembly(in_release_1);
          CREATE INDEX IF NOT EXISTS assembly_seqkit ON assembly(assembly_seqkit_sum);
          CREATE INDEX IF NOT EXISTS assembly_agc ON assembly(agc_filename);
          CREATE INDEX IF NOT EXISTS sylph_sample ON sylph(sample_accession);
          CREATE INDEX IF NOT EXISTS sylph_run ON sylph(run_accession);
          CREATE VIEW IF NOT EXISTS release_1 AS SELECT * FROM assembly WHERE in_release_1=1;
          CREATE VIEW IF NOT EXISTS sylph_unlinked AS SELECT s.* FROM sylph s
            LEFT JOIN assembly a USING(sample_accession) WHERE a.sample_accession IS NULL;
        ''')
        require(con, 'SELECT count(*) FROM assembly', 3720978, 'all sample rows')
        require(con, 'SELECT count(*) FROM release_1', 3092278, 'Release 1 members')
        require(con, 'SELECT count(*) FROM run', 3986805, 'run rows')
        require(con, 'SELECT count(*) FROM agc', 1129, 'AGC archives')
        require(con, 'SELECT sum(genome_count) FROM agc', 3092278, 'AGC membership')
        require(con, "SELECT count(*) FROM release_1 WHERE duplicate_seqkit_sum_status='LIKELY_DUPLICATE'", 0, 'duplicate exclusion')
        require(con, "SELECT count(*) FROM release_1 WHERE dataset='Incr_release.202603'", 344425, '202603 members')
        require(con, "SELECT count(*) FROM release_1 WHERE dataset='Incr_release.202603' AND assembly_accession LIKE 'ERZ%'", 341952, 'ENA successes')
        require(con, "SELECT count(*) FROM release_1 WHERE dataset='Incr_release.202603' AND asm_pipe_filter LIKE '%ENA_ASM_SUBMIT_ERR%' AND comments LIKE '%ENA assembly submission error:%'", 2473, 'ENA failures')
        require(con, "SELECT count(*) FROM release_1 a LEFT JOIN agc g ON a.agc_filename=g.agc_filename WHERE g.agc_filename IS NULL OR a.agc_md5!=g.md5sum OR a.agc_url!=g.aws_url", 0, 'AGC links')
        require(con, 'SELECT count(*) FROM run r LEFT JOIN ena_latest e USING(run_accession) WHERE e.run_accession IS NULL', 0, 'ENA coverage')
        for table in ('assembly_stats', 'checkm2'):
            require(con, f'SELECT count(*) FROM {table} q LEFT JOIN assembly a USING(sample_accession) WHERE a.sample_accession IS NULL', 0, table+' references')
        # Preserve the previous database's ambiguous sample lists verbatim.
        require(con, 'SELECT count(*) FROM sylph_unlinked', 1027, 'historical unlinked Sylph records')
        previous = sqlite3.connect(f'file:{inputs["previous_database"]}?mode=ro', uri=True)
        old_unlinked = previous.execute('SELECT s.* FROM sylph s LEFT JOIN assembly a USING(sample_accession) WHERE a.sample_accession IS NULL').fetchall()
        if sorted(con.execute('SELECT * FROM sylph_unlinked').fetchall(), key=repr) != sorted(old_unlinked, key=repr):
            raise ValueError('unlinked Sylph rows differ from historical database')
        previous.close()
        fixed = con.execute("SELECT assembly_seqkit_sum FROM assembly WHERE sample_accession='SAMN50311804'").fetchone()[0]
        if fixed != 'seqkit.v0.1_DLS_k0_f08322eef17f6451fcb1ea23466e7e74':
            raise ValueError('repaired assembly sum mismatch')
        provenance = {k: {'path': v.name, 'sha256': sha256_file(v)} for k,v in inputs.items()}
        con.execute('CREATE TABLE IF NOT EXISTS release_provenance (name TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)')
        con.execute('INSERT OR REPLACE INTO release_provenance VALUES (?,?)', ('release_1', json.dumps({'release': '1', 'standalone': True, 'inputs': provenance, 'policy': 'in_release_1=1 defines membership; OSF columns are historical; raw ENA snapshots remain archived inputs.'}, sort_keys=True)))
        con.commit()
        if con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('foreign key violations')
        if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        print('Validation passed; exporting database tables', flush=True)
        exports = {}
        for table in ('assembly', 'run', 'ena_latest', 'agc', 'assembly_stats', 'checkm2', 'sylph', 'sylph_unlinked', 'release_provenance', 'release_1'):
            path = output_dir / f'atb.metadata.r1.{table}.tsv.gz'
            cursor = con.execute(f'SELECT * FROM {quote(table)}')
            with path.open('xb') as raw:
                with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
                    with io.TextIOWrapper(compressed, encoding='utf-8', newline='') as handle:
                        writer = csv.writer(handle, delimiter='\t', quoting=csv.QUOTE_NONE, quotechar=None, lineterminator='\n')
                        writer.writerow([c[0] for c in cursor.description])
                        count = 0
                        for row in cursor:
                            writer.writerow(['NA' if v is None else v for v in row]); count += 1
            exports[table] = {'path': path.name, 'rows': count, 'sha256': sha256_file(path)}
            print(f'Exported {table}: {count}', flush=True)
        con.close()
        result = {'status': 'PASS', 'inputs': provenance, 'database': {'path': database.name, 'sha256': sha256_file(database)}, 'exports': exports}
        (output_dir/'atb.metadata.r1.sqlite.report.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
        return result
    finally:
        con.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--finish-existing', action='store_true', help='finish a fully imported database stopped during validation')
    build(**vars(parser.parse_args()))
