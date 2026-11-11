"""Migrate release provenance to filenames without exposing build-machine paths."""
import argparse
import csv
import gzip
import io
import json
import sqlite3
from pathlib import Path

from .io import sha256_file


def portable(value):
    if isinstance(value, dict):
        return {key: Path(item).name if key == 'path' and isinstance(item, str)
                else portable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [portable(item) for item in value]
    return value


def migrate(output_dir):
    database = output_dir / 'atb.metadata.r1.sqlite'
    con = sqlite3.connect(database)
    con.execute('PRAGMA secure_delete=ON')
    with con:
        for name, value in con.execute('SELECT name,metadata_json FROM release_provenance').fetchall():
            con.execute('UPDATE release_provenance SET metadata_json=? WHERE name=?',
                        (json.dumps(portable(json.loads(value)), sort_keys=True), name))
    if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('database integrity failed')
    path = output_dir / 'atb.metadata.r1.release_provenance.tsv.gz'
    with path.open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding='utf-8', newline='') as handle:
                writer = csv.writer(handle, delimiter='\t', quoting=csv.QUOTE_NONE, quotechar=None, lineterminator='\n')
                writer.writerow(('name', 'metadata_json'))
                writer.writerows(con.execute('SELECT * FROM release_provenance'))
    con.close()
    report_path = output_dir / 'atb.metadata.r1.sqlite.report.json'
    report = portable(json.loads(report_path.read_text()))
    report['database']['sha256'] = sha256_file(database)
    report['exports']['release_provenance']['sha256'] = sha256_file(path)
    # This metadata is regenerated after recompressing the modified database.
    report.pop('compressed_database', None)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    print('Provenance paths replaced with filenames; scientific tables unchanged.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    migrate(**vars(parser.parse_args()))
