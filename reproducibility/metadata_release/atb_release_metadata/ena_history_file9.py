"""Preserve the original 661k sample-summary metadata alongside run observations."""
import argparse
import json
import sqlite3
from pathlib import Path

from .ena_history import CORE, export
from .io import iter_tsv_dicts, read_header, sha256_file
from .sqlite_release import quote


def record_policy(con, report, output_dir):
    con.execute('INSERT INTO release_provenance VALUES(?,?)',
                ('ena_history_extension',json.dumps({
                    'counts':report['history_extension']['counts'],
                    'frozen_tables':report['history_extension']['frozen_tables'],
                    'descriptive_policy':'Prefer an association-matching processing/batch source when it supports country; otherwise prefer retained matching full-schema metadata over sparse run metadata. No field-by-field gap filling. Nonmatching fallback is explicitly flagged.',
                    'processing_policy':'Use unique matching processing/batch observations where established; 661k is historical inference and r0.2 does not have a uniformly established processing snapshot.',
                    'sample_summary_policy':'File9 is preserved as sample-level metadata; aggregated run fields are not individual-run observations.',
                    'membership_policy':'Existing assembly, QC, run and AGC table content is unchanged; release_membership matches in_release_1 exactly.'
                },sort_keys=True)))
    con.commit()
    report['exports']['release_provenance']=export(con,'release_provenance',output_dir)


def add(workspace, output_dir):
    source = workspace / 'Figshare_661k/File9_all_metadata_ena_661K.txt'
    database = output_dir/'atb.metadata.r1.sqlite'
    header = read_header(source)
    con = sqlite3.connect(database)
    def guard(action, table, column, db, trigger):
        if db=='main' and table in CORE and action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    con.set_authorizer(guard)
    con.execute('CREATE TABLE ena_661k_sample ('+','.join(quote(c)+' TEXT'+(' PRIMARY KEY NOT NULL' if c=='sample_id' else '') for c in header)+')')
    batch=[]; count=0
    sql='INSERT INTO ena_661k_sample VALUES('+','.join('?' for _ in header)+')'
    for _,row in iter_tsv_dicts(source):
        batch.append(tuple(row[c] for c in header))
        if len(batch)==10000:
            con.executemany(sql,batch); count+=len(batch); batch.clear(); con.commit()
    con.executemany(sql,batch); count+=len(batch)
    if count!=661405: raise ValueError(f'unexpected File9 sample census: {count}')
    con.execute('INSERT INTO ena_source VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('figshare_661k_file9','Original 661k sample-level ENA metadata summary',
                 'published_sample_summary',None,'Exact snapshot observation date unavailable',
                 source.name,sha256_file(source),None,None,
                 'One row per sample. Run-specific fields may aggregate multiple runs; preserve intact and do not infer individual run metadata. Empty fields and quoted NA strings are retained as supplied.',
                 json.dumps(header)))
    upstream = workspace/'Figshare_661k/Json1_ENA_metadata.json.gz'
    con.execute("UPDATE ena_source SET notes=notes || ? WHERE source_id='ena_661k'",
                (' Normalized run metadata originates from '+upstream.name+' (SHA-256 '+sha256_file(upstream)+'), reconciled against the published sample census.',))
    con.execute("UPDATE ena_source SET source_table=NULL,notes=notes || ' Snapshot records were read from current rows of the canonical ENA table, restricted to the original snapshot columns.' WHERE source_id='ena_20260317'")
    con.execute("UPDATE ena_source SET notes=notes || ' Completeness is within the scope of the archived extract; absence is not proof of global ENA deletion.' WHERE source_kind='complete_snapshot'")
    con.executescript('CREATE INDEX file9_country ON ena_661k_sample(country); CREATE INDEX file9_tax_id ON ena_661k_sample(tax_id);')
    missing=con.execute("SELECT count(*) FROM assembly a LEFT JOIN ena_661k_sample s ON a.sample_accession=s.sample_id WHERE a.dataset='661k' AND s.sample_id IS NULL").fetchone()[0]
    if missing: raise ValueError(f'File9 missing {missing} 661k samples')
    inconsistent=con.execute('''SELECT count(*) FROM assembly_ena e JOIN ena_observation o USING(observation_id)
      WHERE e.run_accession IS NOT o.run_accession OR e.source_id IS NOT o.source_id
         OR e.sample_accession IS NOT o.sample_accession OR e.fastq_md5 IS NOT o.fastq_md5
         OR e.fastq_bytes IS NOT o.fastq_bytes OR e.base_count IS NOT o.base_count
         OR e.read_count IS NOT o.read_count''').fetchone()[0]
    if inconsistent: raise ValueError(f'{inconsistent} selected full records disagree with observations')
    for table in ('ena_661k_sample','ena_source'):
        if con.execute('PRAGMA integrity_check('+table+')').fetchall()!=[('ok',)]:
            raise ValueError(f'integrity check failed: {table}')
    report_path=output_dir/'atb.metadata.r1.sqlite.report.json'
    report=json.loads(report_path.read_text())
    report['exports']['ena_661k_sample']=export(con,'ena_661k_sample',output_dir)
    report['exports']['ena_source']=export(con,'ena_source',output_dir)
    report['history_extension']['file9']={'rows':count,'sha256':sha256_file(source),
                                        'scope':'sample-level aggregate; not per-run observation'}
    report['history_extension']['counts']['ena_source']=con.execute('SELECT count(*) FROM ena_source').fetchone()[0]
    report['history_extension']['counts']['ena_661k_sample']=count
    record_policy(con,report,output_dir)
    con.close()
    report['database']['sha256']=sha256_file(database)
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    add(**vars(parser.parse_args()))
