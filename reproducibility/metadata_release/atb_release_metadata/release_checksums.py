"""Verify database-derived release files and write their checksum manifest."""
import argparse
import hashlib
import json
import lzma
from pathlib import Path

from .io import sha256_file


def finalize(output_dir):
    report_path = output_dir / 'atb.metadata.r1.sqlite.report.json'
    report = json.loads(report_path.read_text())
    database = output_dir / Path(report['database']['path']).name
    expected = report['database']['sha256']
    if sha256_file(database) != expected:
        raise ValueError('database changed after export')
    entries = []
    if report.get('distribution',{}).get('include_uncompressed_database',True):
        entries.append((expected,database.name))
    for metadata in list(report['exports'].values())+list(report.get('separate_files',{}).values()):
        path = output_dir / Path(metadata['path']).name
        if sha256_file(path) != metadata['sha256']:
            raise ValueError(f'export changed: {path}')
        entries.append((metadata['sha256'], path.name))
    for metadata in report.get('supporting_files',{}).values():
        path=output_dir/Path(metadata['path']).name
        if sha256_file(path)!=metadata['sha256']:
            raise ValueError(f'supporting evidence changed: {path}')
        entries.append((metadata['sha256'],path.name))
    compressed = database.with_suffix(database.suffix + '.xz')
    digest = hashlib.sha256()
    with lzma.open(compressed, 'rb') as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('compressed SQLite differs from canonical database')
    compressed_sha = sha256_file(compressed)
    print('Compressed database verified against canonical SQLite.',flush=True)
    entries.append((compressed_sha, compressed.name))
    report['compressed_database'] = {'path': compressed.name, 'sha256': compressed_sha,
                                     'decompressed_sha256': expected}
    report['source_files'] = {p.name: sha256_file(p) for p in sorted(Path(__file__).parent.iterdir())
                              if p.is_file() and p.suffix in ('.py','.md')}
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    entries.append((sha256_file(report_path), report_path.name))
    readme = output_dir / 'README.md'
    if readme.exists():
        entries.append((sha256_file(readme), readme.name))
    md5_entries = []
    for _, name in sorted(entries, key=lambda item: item[1]):
        digest = hashlib.md5(usedforsecurity=False)
        with (output_dir / name).open('rb') as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                digest.update(chunk)
        md5_entries.append(f'{digest.hexdigest()}  {name}\n')
    (output_dir / 'MD5SUMS').write_text(''.join(md5_entries))
    print(json.dumps(report['compressed_database'], indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    finalize(**vars(parser.parse_args()))
