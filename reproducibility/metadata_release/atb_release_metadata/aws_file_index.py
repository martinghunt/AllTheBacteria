"""Build upload-ready AWS checksum manifests and a unified file catalogue."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath

HEX = re.compile(r'[0-9a-f]{32}\Z')
LISTING = re.compile(r'(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d:\d\d)\s+(\d+)\s+(.*)\Z')
PROJECTS = {
    'agc':'AGC', 'amr':'AMR', 'annotation':'Annotation/Bakta', 'archaea':'Archaea',
    'assemblies':'Assemblies', 'assembly':'Assembly',
    'biosynthetic-gene-cluster':'Biosynthetic_Gene_Cluster',
    'defense-finder':'DefenseFinder',
    'hypothetical-protein-structures':'Hypothetical Protein Structures',
    'indexes':'Indexes', 'lexicmap':'Indexes/LexicMap', 'media':'Media',
    'metadata':'Metadata', 'mlst':'MLST', 'phylogeneticbatches':'PhylogeneticBatches',
    'tree':'Tree', 'whatsgnu':'WhatsGNU panallelome database',
}
SCOPES = {key:('AllTheBacteria/'+value,) for key,value in PROJECTS.items()}
SCOPES['mlst'] += ('AllTheBacteria/Species_Specific',)
SCOPES['annotation'] = ('AllTheBacteria/Annotation/Bakta',)
EXCLUDED_MD5 = {'allthebacteria-assemblies','allthebacteria-lexicmap'}
COLUMNS = ('filename','url','md5','size_bytes','size(MB)','bucket')
INVENTORY_PREFIX = 'allthebacteria-assemblies/assemblies-list/'


def exclude_automated_inventory(con):
    """Inventory is a build input, not a published download or checksum entry."""
    return con.execute('DELETE FROM objects WHERE bucket=? AND substr(key,1,?)=?',
                       ('allthebacteria-metadata', len(INVENTORY_PREFIX), INVENTORY_PREFIX)).rowcount


def digest(path, algorithm='sha256'):
    h=hashlib.new(algorithm)
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(4*1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def safe_key(key):
    if not key or key.startswith('/') or any(c in key for c in '\t\r\n\0') or '..' in key.split('/'):
        raise ValueError('Unsupported/unsafe S3 key: '+repr(key))
    return key


def object_url(bucket,key):
    return f'https://{bucket}.s3.eu-west-2.amazonaws.com/'+urllib.parse.quote(safe_key(key),safe='/')


def parse_listing(path):
    seen=set()
    with path.open() as handle:
        for lineno,line in enumerate(handle,1):
            line=line.rstrip('\n')
            match=LISTING.fullmatch(line)
            if not match: raise ValueError(f'{path.name}:{lineno}: invalid recursive S3 listing')
            day,clock,size,key=match.groups();safe_key(key)
            if key in seen: raise ValueError('Duplicate listing key: '+key)
            seen.add(key)
            yield key,int(size),day+' '+clock


def parse_md5(payload):
    result={}
    for line in payload.decode().splitlines():
        escaped=line.startswith('\\')
        if escaped:line=line[1:]
        match=re.fullmatch(r'([0-9a-fA-F]{32}) [ *](.+)',line)
        if not match:raise ValueError('Invalid MD5SUMS record: '+repr(line))
        value,key=match.groups()
        if escaped:
            decoded=[];i=0
            while i<len(key):
                if key[i]=='\\':
                    if i+1>=len(key) or key[i+1]!='\\':raise ValueError('Unsupported escaped manifest filename')
                    i+=1
                decoded.append(key[i]);i+=1
            key=''.join(decoded)
        if key.startswith('./'):key=key[2:]
        safe_key(key);value=value.lower()
        if key in result and result[key]!=value:raise ValueError('Conflicting manifest MD5: '+key)
        result[key]=value
    return result


def md5_line(value,key):
    if not HEX.fullmatch(value):raise ValueError('Not an MD5: '+value)
    safe_key(key)
    if '\\' in key:return '\\'+value+'  '+key.replace('\\','\\\\')+'\n'
    return value+'  '+key+'\n'


class Client:
    """Cache exact response bytes and portable request provenance."""
    def __init__(self,cache):
        self.cache=cache;cache.mkdir(parents=True,exist_ok=True)
        self.seeds={
            ('allthebacteria-agc','MD5SUMS'):'allthebacteria-agc.MD5SUMS',
            ('allthebacteria-indexes','MD5SUMS'):'allthebacteria-indexes.MD5SUMS',
            ('allthebacteria-annotation','Bakta-v1.9.4.DB-v5.1/md5sum.tsv'):'allthebacteria-annotation.md5sum.tsv',
        }
    def get(self,bucket,key,size,expected_md5=None):
        url=object_url(bucket,key)
        stem=hashlib.sha256(url.encode()).hexdigest()
        seed=self.cache/self.seeds.get((bucket,key),('missing-seed-'+stem))
        path=seed if seed.exists() else self.cache/(stem+'.data')
        if not path.exists():
            if size>128*1024*1024:raise ValueError('Refusing unbounded data download: '+key)
            error=None
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(url,timeout=60) as response, path.with_suffix(path.suffix+'.tmp').open('wb') as output:
                        if int(response.headers.get('Content-Length',size))!=size:raise ValueError('Remote size differs from listing: '+key)
                        total=0
                        while chunk:=response.read(1024*1024):
                            total+=len(chunk)
                            if total>size:raise ValueError('Remote payload exceeds listed size: '+key)
                            output.write(chunk)
                    if total!=size:raise ValueError('Truncated download: '+key)
                    path.with_suffix(path.suffix+'.tmp').replace(path)
                    break
                except Exception as exc:
                    error=exc
                    if attempt==3:raise
                    time.sleep(2**attempt)
            if not path.exists():raise RuntimeError(str(error))
        if path.stat().st_size!=size:raise ValueError('Cached size mismatch: '+key)
        checksum=digest(path,'md5')
        if expected_md5 is not None and checksum!=expected_md5:raise ValueError('Cached/downloaded MD5 mismatch: '+key)
        meta=self.cache/(stem+'.request.json')
        if not meta.exists():
            meta.write_text(json.dumps({'url':url,'response_filename':path.name,
                'retrieved_at':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),
                'size_bytes':size,'md5':checksum,'sha256':digest(path)},indent=2,sort_keys=True)+'\n')
        return path


def old_match(bucket,key,size,rows):
    """Prefer exact paths; use a unique checksum/size match only within a bucket's project."""
    scopes=SCOPES[bucket.removeprefix('allthebacteria-')]
    candidates=[]
    for row in rows:
        scope=next((s for s in scopes if row['project']==s or row['project'].startswith(s+'/')),None)
        if scope is None:continue
        name=row['filename']
        suffix=row['project'][len(scope):].lstrip('/').replace(';','_')
        projected='/'.join(x for x in (suffix,name) if x)
        rank=0 if key==name or key==projected else 1
        if rank and PurePosixPath(key).name!=PurePosixPath(name).name:continue
        candidates.append((rank,row))
    if not candidates:return None
    rank=min(r for r,_ in candidates)
    candidates=[r for n,r in candidates if n==rank]
    candidates=[r for r in candidates if abs(Decimal(r['size(MB)'])-Decimal(size)/1048576)<=Decimal('0.00501')]
    values={r['md5'].lower() for r in candidates}
    if len(values)!=1:return None
    value=values.pop()
    if not HEX.fullmatch(value):raise ValueError('Invalid source MD5')
    return value,sorted({r['url'] for r in candidates})


def assign(con,bucket,key,value,source):
    if not HEX.fullmatch(value):raise ValueError('Invalid MD5 from '+source)
    row=con.execute('SELECT md5 FROM objects WHERE bucket=? AND key=?',(bucket,key)).fetchone()
    if row is None:raise ValueError('Checksum refers to an unlisted object: '+bucket+'/'+key)
    if row[0] is not None and row[0]!=value:raise ValueError('Conflicting MD5 sources: '+bucket+'/'+key)
    con.execute('UPDATE objects SET md5=?,md5_source=? WHERE bucket=? AND key=?',(value,source,bucket,key))



def add_lexicmap_checksums(con, path):
    """Select 2024-08 checksums from a captured local checksum-job output."""
    bucket = 'allthebacteria-lexicmap'
    listed = {key for key, in con.execute("SELECT key FROM objects WHERE bucket=? AND key!='MD5SUMS'", (bucket,))}
    checksums = {}
    for line in path.read_text().splitlines():
        # Other releases may still be running, including an incomplete last line.
        if '/2024-08/' not in line:
            continue
        match = re.fullmatch(r'([0-9a-fA-F]{32}) [ *](.+)', line)
        if match is None:
            raise ValueError('Invalid 2024 LexicMap checksum record')
        value, original = match.groups()
        key = safe_key('202408/' + original.split('/2024-08/', 1)[1])
        if key in checksums:
            raise ValueError('Duplicate LexicMap checksum path: ' + key)
        checksums[key] = value.lower()
    missing = listed - checksums.keys()
    if missing:
        raise ValueError('Missing LexicMap checksums: ' + repr(sorted(missing)))
    for key in sorted(listed):
        assign(con, bucket, key, checksums[key], 'lexicmap_manifest')
    return {'filename': path.name, 'sha256': digest(path), 'matched_objects': len(listed),
            'unlisted_keys': sorted(checksums.keys() - listed),
            'policy': 'Only 2024-08 entries used; unlisted files are not added to the catalogue.'}


def add_inventory(con,bucket,client,cache):
    sources=[]
    objects=con.execute("SELECT key,size FROM objects WHERE bucket=? AND key LIKE '%/manifest.json' ORDER BY key",(bucket,)).fetchall()
    for index,(key,size) in enumerate(objects,1):
        p=client.get(bucket,key,size);value=digest(p,'md5')
        assign(con,bucket,key,value,'downloaded_bytes')
        m=json.loads(p.read_text())
        checksum_key=key.removesuffix('.json')+'.checksum'
        checksum_row=con.execute('SELECT size FROM objects WHERE bucket=? AND key=?',(bucket,checksum_key)).fetchone()
        if checksum_row is None:raise ValueError('Inventory manifest checksum file missing')
        proof=client.get(bucket,checksum_key,checksum_row[0])
        if proof.read_text().strip()!=value:raise ValueError('Inventory manifest proof mismatch')
        assign(con,bucket,checksum_key,digest(proof,'md5'),'downloaded_bytes')
        for item in m['files']:
            row=con.execute('SELECT size FROM objects WHERE bucket=? AND key=?',(bucket,item['key'])).fetchone()
            if row is None or row[0]!=item['size']:raise ValueError('Inventory data file differs from listing')
            assign(con,bucket,item['key'],item['MD5checksum'].lower(),'s3_inventory_manifest')
        if m['sourceBucket'].removeprefix('arn:aws:s3:::')=='allthebacteria-assemblies':sources.append((int(m['creationTimestamp']),key,m))
        if index%25==0:print('Verified inventory manifests: '+str(index),flush=True)
    if not sources:raise ValueError('No assembly inventory found')
    timestamp,key,m=max(sources,key=lambda x:x[0])
    manifest_key=key
    fields=[x.strip() for x in m['fileSchema'].split(',')]
    for expected in ('Bucket','Key','Size','ETag'):
        if expected not in fields:raise ValueError('Inventory schema missing '+expected)
    before=con.total_changes
    for i,item in enumerate(m['files'],1):
        # Reuse the manually downloaded, checksum-verified latest inventory if present.
        seed=cache/f'assemblies_inventory.part{i}.csv.gz'
        if seed.exists() and digest(seed,'md5')==item['MD5checksum']:
            p=seed
        else:p=client.get(bucket,item['key'],item['size'],item['MD5checksum'])
        if p.stat().st_size!=item['size'] or digest(p,'md5')!=item['MD5checksum']:raise ValueError('Assembly inventory hash mismatch')
        batch=[]
        with gzip.open(p,'rt',newline='') as h:
            for raw in csv.reader(h):
                if len(raw)!=len(fields):raise ValueError('Inventory field count mismatch')
                row=dict(zip(fields,raw))
                if row['Bucket']!='allthebacteria-assemblies':raise ValueError('Unexpected inventory bucket')
                key=safe_key(urllib.parse.unquote(row['Key']))
                batch.append((row['Bucket'],key,int(row['Size']),row.get('LastModifiedDate'),
                              's3_inventory',row['ETag']))
                if len(batch)>=10000:
                    con.executemany('INSERT INTO objects(bucket,key,size,modified,listing_source,etag) VALUES(?,?,?,?,?,?)',batch);batch.clear()
            if batch:con.executemany('INSERT INTO objects(bucket,key,size,modified,listing_source,etag) VALUES(?,?,?,?,?,?)',batch)
        print('Loaded assembly inventory part '+str(i),flush=True)
    return {'manifest':object_url(bucket,manifest_key),
            'creation_timestamp_ms':timestamp,'objects':con.total_changes-before,'etag_policy':'ETags retained separately, never promoted to full-file MD5.'}


def add_known_fastas(con,database):
    source=sqlite3.connect(f'file:{database.resolve()}?mode=ro',uri=True)
    before=con.total_changes;seen=0;batch=[]
    for sample,url in source.execute("SELECT sample_accession,aws_url FROM assembly WHERE aws_url IS NOT NULL AND aws_url NOT IN ('NA','TODO','')"):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc!='allthebacteria-assemblies.s3.eu-west-2.amazonaws.com' or parsed.query or parsed.fragment:
            raise ValueError('Unexpected assembly URL: '+url)
        key=safe_key(urllib.parse.unquote(parsed.path.lstrip('/')))
        if key!=sample+'.fa.gz':raise ValueError('Assembly URL is not the expected root FASTA')
        batch.append(('allthebacteria-assemblies',key,'assembly_metadata'));seen+=1
        if len(batch)>=10000:
            con.executemany('INSERT OR IGNORE INTO objects(bucket,key,listing_source) VALUES(?,?,?)',batch);batch.clear()
    if batch:con.executemany('INSERT OR IGNORE INTO objects(bucket,key,listing_source) VALUES(?,?,?)',batch)
    source.close()
    return {'known_urls':seen,'additional_objects':con.total_changes-before,
            'policy':'Only actual non-missing AWS URLs are used. No bucket listing or per-genome requests.'}



def write_catalogues(con, output_dir):
    """Write the CLI master index and a separate index of individual FASTAs."""
    summaries = {}
    for name, stem, comparison in (
        ('master', 'all_atb_files', '!='),
        ('assemblies', 'assembly_files', '='),
    ):
        tsv = output_dir / (stem + '.tsv')
        gz = output_dir / (stem + '.tsv.gz')
        count = 0
        with tsv.open('w', newline='') as plain, gz.open('wb') as raw:
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=9, mtime=0) as zipped:
                with io.TextIOWrapper(zipped, encoding='utf-8', newline='') as compressed:
                    writers = [csv.writer(h, delimiter='\t', quoting=csv.QUOTE_NONE,
                                          quotechar=None, lineterminator='\n')
                               for h in (plain, compressed)]
                    for writer in writers:
                        writer.writerow(COLUMNS)
                    query = f'SELECT bucket,key,size,md5 FROM objects WHERE bucket {comparison} ? ORDER BY bucket,key'
                    for bucket, key, size, value in con.execute(query, ('allthebacteria-assemblies',)):
                        row = (key, object_url(bucket, key), value or 'NA',
                               size if size is not None else 'NA',
                               format(Decimal(size)/1048576, '.2f') if size is not None else 'NA', bucket)
                        for writer in writers:
                            writer.writerow(row)
                        count += 1
        summaries[name] = {'filename': tsv.name, 'compressed_filename': gz.name,
                           'rows': count, 'sha256': digest(tsv), 'compressed_sha256': digest(gz)}
    return summaries


def build(osf_index,aws_lists,assembly_db,cache_dir,work_db,output_dir,lexicmap_md5=None):
    excluded_md5 = EXCLUDED_MD5 - ({'allthebacteria-lexicmap'} if lexicmap_md5 else set())
    if work_db.exists():raise ValueError('Work database already exists; inspect before restarting')
    if output_dir.exists() and any(output_dir.iterdir()):raise ValueError('Output directory is not empty')
    output_dir.mkdir(parents=True,exist_ok=True);work_db.parent.mkdir(parents=True,exist_ok=True)
    buckets=(aws_lists/'buckets').read_text().splitlines()
    expected={'allthebacteria-'+x for x in PROJECTS}
    # The supplied listing-job file deliberately omits the large FASTA bucket.
    if len(buckets)!=len(set(buckets)) or set(buckets) not in (expected,expected-{'allthebacteria-assemblies'}):
        raise ValueError('Bucket set differs from the reviewed inputs')
    buckets=sorted(expected)
    source_rows=list(csv.DictReader(osf_index.open(),delimiter='\t',quoting=csv.QUOTE_NONE))
    con=sqlite3.connect(work_db)
    con.execute('PRAGMA cache_size=-131072')
    con.execute('''CREATE TABLE objects(bucket TEXT NOT NULL,key TEXT NOT NULL,size INTEGER,modified TEXT,
       md5 TEXT,md5_source TEXT,listing_source TEXT NOT NULL,etag TEXT,PRIMARY KEY(bucket,key)) WITHOUT ROWID''')
    audit=[]
    with con:
        for bucket in buckets:
            if bucket=='allthebacteria-assemblies':continue
            for key,size,modified in parse_listing(aws_lists/(bucket+'.list')):
                con.execute('INSERT INTO objects(bucket,key,size,modified,listing_source) VALUES(?,?,?,?,?)',(bucket,key,size,modified,'aws_file_list'))
                if bucket=='allthebacteria-lexicmap' or key=='MD5SUMS':continue
                match=old_match(bucket,key,size,source_rows)
                if match:
                    value,urls=match;assign(con,bucket,key,value,'osf_index')
                    audit.append((bucket,key,value,'osf_index',';'.join(urls)))
    lexicmap_source = None
    if lexicmap_md5 is not None:
        with con:
            lexicmap_source = add_lexicmap_checksums(con,lexicmap_md5)
        audit.extend(('allthebacteria-lexicmap',key,value,'lexicmap_manifest',lexicmap_md5.name)
                     for key,value in con.execute("SELECT key,md5 FROM objects WHERE bucket='allthebacteria-lexicmap'"))
    print('Loaded file lists and unambiguous, size-matching OSF checksums.',flush=True)
    client=Client(cache_dir)
    with con:
        for bucket in ('allthebacteria-agc','allthebacteria-indexes'):
            size=con.execute('SELECT size FROM objects WHERE bucket=? AND key=?',(bucket,'MD5SUMS')).fetchone()[0]
            p=client.get(bucket,'MD5SUMS',size)
            for key,value in parse_md5(p.read_bytes()).items():
                assign(con,bucket,key,value,'bucket_manifest')
                audit.append((bucket,key,value,'bucket_manifest',object_url(bucket,'MD5SUMS')))
        bucket='allthebacteria-annotation';key='Bakta-v1.9.4.DB-v5.1/md5sum.tsv'
        size=con.execute('SELECT size FROM objects WHERE bucket=? AND key=?',(bucket,key)).fetchone()[0]
        p=client.get(bucket,key,size)
        assign(con,bucket,key,digest(p,'md5'),'downloaded_bytes')
        for row in csv.DictReader(p.open(),delimiter='\t',quoting=csv.QUOTE_NONE):
            target=str(PurePosixPath(key).parent/row['Filename'])
            assign(con,bucket,target,row['md5sum'].lower(),'bakta_manifest')
            audit.append((bucket,target,row['md5sum'].lower(),'bakta_manifest',object_url(bucket,key)))
        print('Loaded AGC, index and Bakta manifests.',flush=True)
        inventory=add_inventory(con,'allthebacteria-metadata',client,cache_dir)
        inventory['excluded_download_objects'] = exclude_automated_inventory(con)
        audit = [row for row in audit if not (row[0]=='allthebacteria-metadata' and row[1].startswith(INVENTORY_PREFIX))]
    print('Loaded latest assembly inventory; recovering remaining small-file checksums.',flush=True)
    pending=con.execute("SELECT bucket,key,size FROM objects WHERE md5 IS NULL AND key!='MD5SUMS' AND bucket NOT IN ("+','.join('?' for _ in excluded_md5)+") ORDER BY bucket,key",tuple(sorted(excluded_md5))).fetchall()
    large=[row for row in pending if row[2] is None or row[2]>1024*1024]
    if large:
        (output_dir/'unresolved_checksums.json').write_text(json.dumps(large,indent=2)+'\n')
        raise ValueError('Unresolved large-file MD5s; inspect unresolved_checksums.json')
    def fetch(row):
        bucket,key,size=row;p=client.get(bucket,key,size)
        return bucket,key,digest(p,'md5')
    with con,ThreadPoolExecutor(max_workers=8) as pool:
        for bucket,key,value in pool.map(fetch,pending):
            assign(con,bucket,key,value,'downloaded_bytes');audit.append((bucket,key,value,'downloaded_bytes',object_url(bucket,key)))
    with con:
        known=add_known_fastas(con,assembly_db)
    print('Known FASTA URLs merged; writing bucket manifests.',flush=True)
    summaries={}
    with con:
        for bucket in buckets:
            total,missing=con.execute("SELECT count(*),sum(CASE WHEN md5 IS NULL AND key!='MD5SUMS' THEN 1 ELSE 0 END) FROM objects WHERE bucket=?",(bucket,)).fetchone()
            if bucket in excluded_md5:
                summaries[bucket]={'listed_objects':total,'manifest':'excluded','unknown_md5':missing or 0};continue
            if missing:raise ValueError('Incomplete MD5 manifest: '+bucket)
            path=output_dir/bucket/'MD5SUMS';path.parent.mkdir()
            entries=con.execute("SELECT key,md5 FROM objects WHERE bucket=? AND key!='MD5SUMS' ORDER BY key",(bucket,)).fetchall()
            path.write_text(''.join(md5_line(value,key) for key,value in entries))
            # Existing root manifests are replaced by the exact generated payload,
            # including in the planned post-upload catalogue. They exclude themselves.
            value=digest(path,'md5')
            con.execute('INSERT INTO objects(bucket,key,size,md5,md5_source,listing_source) VALUES(?,?,?,?,?,?) ON CONFLICT(bucket,key) DO UPDATE SET size=excluded.size,md5=excluded.md5,md5_source=excluded.md5_source,listing_source=excluded.listing_source',
                        (bucket,'MD5SUMS',path.stat().st_size,value,'generated_manifest','generated_manifest'))
            summaries[bucket]={'objects_checked':len(entries),'manifest_entries':len(entries),
                'manifest':str(path.relative_to(output_dir)),'md5':value,'sha256':digest(path)}
    if con.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('Catalogue database integrity failure')
    catalogues=write_catalogues(con,output_dir)
    count=catalogues['master']['rows']
    with (output_dir/'checksum_sources.tsv').open('w',newline='') as h:
        w=csv.writer(h,delimiter='\t',lineterminator='\n',quoting=csv.QUOTE_NONE,quotechar=None)
        w.writerow(('bucket','filename','md5','source','source_url'));w.writerows(sorted(audit))
    inputs={'osf_index':{'filename':osf_index.name,'sha256':digest(osf_index)},
            'bucket_list':{'filename':'buckets','sha256':digest(aws_lists/'buckets')},
            'file_lists':{p.name:digest(p) for p in sorted(aws_lists.glob('*.list'))}}
    if lexicmap_source is not None:inputs['lexicmap_md5']=lexicmap_source
    report={'inputs':inputs,'buckets':summaries,'assembly_inventory':inventory,'assembly_metadata':known,
            'catalogue_rows':count,'catalogues':catalogues,
            'md5_exclusions':sorted(excluded_md5),'policy':{
                'osf_matching':'Project-scoped exact paths preferred; otherwise require one checksum among same-basename, size-matching candidates. Ambiguity is never resolved by picking a row.',
                'etag':'Kept as ETag only; never substituted for MD5.',
                'generated_manifests':'Catalogue includes the generated root MD5SUMS payloads, which must be uploaded before publishing the catalogue. Each excludes its own root MD5SUMS.',
                'catalogue_self':'The catalogue has no assigned bucket/key and does not list itself.',
                'catalogue_split':'Master excludes allthebacteria-assemblies; individual FASTAs are in assembly_files.tsv.',
                'automated_inventory':'allthebacteria-metadata/allthebacteria-assemblies/assemblies-list/ is used as build input but excluded from published catalogues and MD5SUMS.',
                'unknowns':'NA denotes unavailable MD5 checksums or sizes; detailed listing provenance remains in the build database.'}}
    (output_dir/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    con.close()
    print(json.dumps({'rows':count,'manifests':len(buckets)-len(excluded_md5),'assembly_inventory':inventory,'additional_known_fastas':known},indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('osf-index','aws-lists','assembly-db','cache-dir','work-db','output-dir'):
        p.add_argument('--'+arg,type=Path,required=True)
    p.add_argument('--lexicmap-md5',type=Path,help='Captured checksum-job output; only 2024-08 entries are used')
    build(**vars(p.parse_args()))
