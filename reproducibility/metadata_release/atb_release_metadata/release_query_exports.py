"""Export a convenient Release 1 metadata join directly from canonical SQLite."""
import argparse
import json
import sqlite3
from pathlib import Path

from .ena_history import export
from .sqlite_release import quote


def build(output_dir):
    database=output_dir/'atb.metadata.r1.sqlite'
    con=sqlite3.connect(f'file:{database.resolve()}?mode=ro',uri=True)
    columns=[r[1] for r in con.execute('PRAGMA table_info(assembly_ena_metadata)')]
    if not columns: raise ValueError('assembly_ena_metadata table is missing')
    projection=[]
    for column in columns:
        if column=='assembly_sample_accession': value='a.sample_accession'
        elif column=='selection_basis': value="coalesce(e.selection_basis,'unavailable')"
        elif column=='notes': value="coalesce(e.notes,'No recoverable run accession; metadata not assigned.')"
        else: value='e.'+quote(column)
        projection.append(value+' AS '+quote(column))
    projection += ['a.dataset AS assembly_dataset','a.sylph_species AS assembly_sylph_species',
                   'a.hq_filter AS assembly_hq_filter','a.aws_url AS assembly_aws_url']
    query='SELECT '+','.join(projection)+''' FROM release_membership m
      JOIN assembly a ON a.sample_accession=m.sample_accession
      LEFT JOIN assembly_ena_metadata e ON e.assembly_sample_accession=a.sample_accession
      WHERE m.release_id='1' '''
    con.execute('CREATE TEMP VIEW release_1_assembly_ena_metadata AS '+query)
    count=con.execute('SELECT count(DISTINCT assembly_sample_accession) FROM release_1_assembly_ena_metadata').fetchone()[0]
    expected=con.execute("SELECT count(*) FROM release_membership WHERE release_id='1'").fetchone()[0]
    if count!=expected: raise ValueError('convenience export loses release samples')
    metadata=export(con,'release_1_assembly_ena_metadata',output_dir)
    metadata['query']=query
    metadata['distinct_assembly_samples']=count
    con.close()
    report_path=output_dir/'atb.metadata.r1.sqlite.report.json'
    report=json.loads(report_path.read_text())
    report['exports']['release_1_assembly_ena_metadata']=metadata
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    build(**vars(parser.parse_args()))
