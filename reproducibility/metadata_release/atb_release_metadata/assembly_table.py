from __future__ import annotations

import ast
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .io import iter_tsv_dicts, open_text, open_tsv_writer, read_header, sha256_file


ASSEMBLY_COLUMNS = (
    "sample_accession", "run_accession", "assembly_accession",
    "assembly_seqkit_sum", "duplicate_seqkit_sum_status", "asm_pipe_filter", "asm_fasta_on_osf", "dataset", "in_release_1",
    "scientific_name", "sylph_species_pre_202505", "in_hq_pre_202505",
    "sylph_species", "sylph_filter", "hq_filter", "osf_tarball_filename",
    "osf_tarball_url", "aws_url", "comments",
)
PREVIOUS_ASSEMBLY_COLUMNS = tuple(
    x for x in ASSEMBLY_COLUMNS if x not in {"duplicate_seqkit_sum_status", "in_release_1"}
)
METADATA_FILTERS = {"NO_RUNS", "RUN_REMOVED", "RMMS", "META_FAIL", "RUN_CHANGE"}
PIPELINE_STATUS = {
    "downloaded_reads_fail": "ASM_DLR",
    "finished": "PASS",
    "reads_fastq_gzip_check_fail": "ASM_FQ_GZIP",
    "shovill_fail": "ASM_SHV",
    "timeout": "ASM_TIME",
}
EMPTY_SEQKIT_SUM = "seqkit.v0.1_LS_k0_56fc8fa222734a7d4b9ddedcca604bbf"
DUP_SEQKIT_SUM = "DUP_SEQKIT_SUM"
PROCESS_METADATA_CHANGE_FILTERS = {
    "fastq_md5": "FASTQ_MD5_CHANGED",
    "fastq_bytes": "FASTQ_BYTES_CHANGED",
}
RELEASE_1_LEGACY_DATASETS = {"661k", "r0.2", "Incr_release.202408", "Incr_release.202505"}


class AssemblyBuildError(RuntimeError):
    """Raised when assembly inputs cannot be reconciled exactly."""


@dataclass(frozen=True)
class AssemblyInputs:
    previous_assembly: Path
    run_table: Path
    ena_latest: Path
    executed: Path
    process_metadata: Path
    status: Path
    sylph: Path
    sylph_no_matches: Path
    genome_sizes: Path
    assembly_stats: Path
    checkm2: Path
    seqkit: Path
    release: str = "202603"
    current_snapshot_column: str = "in_ena_20260317"


@dataclass(frozen=True)
class AssemblyBuildPaths:
    database: Path
    output: Path
    report: Path


def _split(value: str) -> list[str]:
    return sorted({x for x in value.replace(",", ";").split(";") if x})


def _split_runs(value: str) -> list[str]:
    return [x for x in _split(value) if x not in {"NA", "unknown"}]


def _filters(value: str) -> set[str]:
    return set() if value in {"", "PASS"} else set(value.split(","))


def _format_filters(values: Iterable[str]) -> str:
    values = sorted(set(values))
    return "PASS" if not values else ",".join(values)


def _float(value: str, context: str) -> float:
    try:
        return float(value)
    except ValueError as error:
        raise AssemblyBuildError(f"{context}: expected number, got {value!r}") from error


def _int(value: str, context: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise AssemblyBuildError(f"{context}: expected integer, got {value!r}") from error


def _new_db(path: Path) -> sqlite3.Connection:
    if path.exists():
        raise AssemblyBuildError(f"refusing to overwrite work database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(
        """
        CREATE TABLE run (
          run TEXT PRIMARY KEY, samples TEXT NOT NULL, pass INTEGER NOT NULL,
          meta_atb INTEGER NOT NULL, present INTEGER NOT NULL, reasons TEXT NOT NULL
        );
        CREATE TABLE run_sample (
          sample TEXT NOT NULL, run TEXT NOT NULL, PRIMARY KEY(sample,run)
        ) WITHOUT ROWID;
        CREATE INDEX run_sample_run ON run_sample(run);
        CREATE TABLE ena (
          run TEXT PRIMARY KEY, current_samples TEXT NOT NULL, scientific_name TEXT NOT NULL,
          fastq_md5 TEXT NOT NULL, fastq_bytes TEXT NOT NULL,
          base_count TEXT NOT NULL, read_count TEXT NOT NULL, first_public TEXT NOT NULL
        );
        CREATE TABLE old_sample(sample TEXT PRIMARY KEY);
        CREATE TABLE executed(
          sample TEXT PRIMARY KEY, run TEXT NOT NULL, status TEXT,
          scientific_name TEXT, process_sample TEXT, fastq_md5 TEXT,
          fastq_bytes TEXT, base_count TEXT, read_count TEXT,
          process_missing TEXT NOT NULL DEFAULT '', process_seen INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX executed_run ON executed(run);
        CREATE TABLE sylph(
          sample TEXT NOT NULL, species TEXT NOT NULL, run TEXT NOT NULL,
          sequence_abundance REAL NOT NULL, eff_cov REAL NOT NULL,
          genome_accession TEXT NOT NULL, PRIMARY KEY(sample,species)
        ) WITHOUT ROWID;
        CREATE TABLE no_match(sample TEXT PRIMARY KEY);
        CREATE TABLE stats(sample TEXT PRIMARY KEY,total_length INTEGER,number INTEGER,n50 INTEGER);
        CREATE TABLE checkm(sample TEXT PRIMARY KEY,completeness TEXT,contamination TEXT,notes TEXT);
        CREATE TABLE seqkit(sample TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE INDEX seqkit_value ON seqkit(value);
        CREATE TABLE seqkit_member(
          seqkit_sum TEXT NOT NULL, sample TEXT PRIMARY KEY, run TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX seqkit_member_sum ON seqkit_member(seqkit_sum);
        CREATE TABLE duplicate_assignment(
          sample TEXT PRIMARY KEY, status TEXT NOT NULL, matching_members TEXT NOT NULL
        ) WITHOUT ROWID;
        """
    )
    return con


def _require(path: Path, columns: set[str]) -> None:
    missing = columns.difference(read_header(path))
    if missing:
        raise AssemblyBuildError(f"{path}: missing columns: {', '.join(sorted(missing))}")


def _insert_unique(con: sqlite3.Connection, sql: str, values: tuple, context: str) -> None:
    try:
        con.execute(sql, values)
    except sqlite3.IntegrityError as error:
        raise AssemblyBuildError(f"{context}: duplicate or conflicting record") from error


def _load_core(con: sqlite3.Connection, inputs: AssemblyInputs) -> None:
    _require(inputs.previous_assembly, set(PREVIOUS_ASSEMBLY_COLUMNS))
    _require(inputs.run_table, {"run_accession", "sample_accession", "pass", "meta_pass_atb", inputs.current_snapshot_column, "failure_reasons"})
    _require(inputs.ena_latest, {"run_accession", "sample_accession", "scientific_name", "fastq_md5", "fastq_bytes", "base_count", "read_count", "first_public", "atb_present_in_current_snapshot"})
    con.execute("BEGIN")
    for n, row in iter_tsv_dicts(inputs.previous_assembly):
        _insert_unique(con, "INSERT INTO old_sample VALUES(?)", (row["sample_accession"],), f"{inputs.previous_assembly}:{n}")
    con.commit()
    con.execute("BEGIN")
    for n, row in iter_tsv_dicts(inputs.run_table):
        if any(row[x] not in {"0", "1"} for x in ("pass", "meta_pass_atb", inputs.current_snapshot_column)):
            raise AssemblyBuildError(f"{inputs.run_table}:{n}: invalid Boolean flag")
        samples = _split(row["sample_accession"])
        _insert_unique(con, "INSERT INTO run VALUES(?,?,?,?,?,?)", (
            row["run_accession"], ",".join(samples), int(row["pass"]),
            int(row["meta_pass_atb"]), int(row[inputs.current_snapshot_column]), row["failure_reasons"]
        ), f"{inputs.run_table}:{n}")
        for sample in samples:
            _insert_unique(con, "INSERT INTO run_sample VALUES(?,?)", (sample,row["run_accession"]), f"{inputs.run_table}:{n}")
    con.commit()
    con.execute("BEGIN")
    for n, row in iter_tsv_dicts(inputs.ena_latest):
        current = row["sample_accession"] if row["atb_present_in_current_snapshot"] == "1" else ""
        _insert_unique(con, "INSERT INTO ena VALUES(?,?,?,?,?,?,?,?)", (
            row["run_accession"], current, row["scientific_name"], row["fastq_md5"],
            row["fastq_bytes"], row["base_count"], row["read_count"], row["first_public"]
        ), f"{inputs.ena_latest}:{n}")
    con.commit()
    missing = con.execute("SELECT count(*) FROM run LEFT JOIN ena USING(run) WHERE ena.run IS NULL").fetchone()[0]
    if missing:
        raise AssemblyBuildError(f"{missing} run-table rows are absent from ena_latest")


def _load_execution(con: sqlite3.Connection, inputs: AssemblyInputs) -> None:
    con.execute("BEGIN")
    with inputs.executed.open(encoding="utf-8") as handle:
        for n, line in enumerate(handle, 1):
            fields = line.split()
            if len(fields) != 2:
                raise AssemblyBuildError(f"{inputs.executed}:{n}: expected sample and run")
            _insert_unique(con, "INSERT INTO executed(sample,run) VALUES(?,?)", tuple(fields), f"{inputs.executed}:{n}")
    con.commit()

    _require(inputs.status, {"Sample", "Status"})
    seen: set[str] = set()
    for n, row in iter_tsv_dicts(inputs.status):
        sample, status = row["Sample"], row["Status"]
        if status not in PIPELINE_STATUS:
            raise AssemblyBuildError(f"{inputs.status}:{n}: unknown status {status!r}")
        if sample in seen or con.execute("UPDATE executed SET status=? WHERE sample=?", (status,sample)).rowcount != 1:
            raise AssemblyBuildError(f"{inputs.status}:{n}: duplicate or unexpected sample {sample}")
        seen.add(sample)
    con.commit()

    con.execute("BEGIN")
    with open_text(inputs.process_metadata) as handle:
        for n, line in enumerate(handle, 1):
            try:
                row = ast.literal_eval(line)
            except (ValueError, SyntaxError) as error:
                raise AssemblyBuildError(f"{inputs.process_metadata}:{n}: invalid Python dict") from error
            if not isinstance(row, dict):
                raise AssemblyBuildError(f"{inputs.process_metadata}:{n}: expected dict")
            if "run_accession" not in row:
                raise AssemblyBuildError(f"{inputs.process_metadata}:{n}: missing run_accession")
            optional = ("sample_accession", "scientific_name", "fastq_md5", "fastq_bytes", "base_count", "read_count")
            missing = sorted(key for key in optional if key not in row)
            changed = con.execute("""
              UPDATE executed SET scientific_name=?,process_sample=?,fastq_md5=?,fastq_bytes=?,base_count=?,read_count=?,process_missing=?,process_seen=1
              WHERE run=? AND process_seen=0
            """, (row.get("scientific_name"),row.get("sample_accession"),row.get("fastq_md5"),row.get("fastq_bytes"),row.get("base_count"),row.get("read_count"),",".join(missing),row["run_accession"])).rowcount
            if changed != 1:
                raise AssemblyBuildError(f"{inputs.process_metadata}:{n}: unexpected or duplicate sample/run")
    con.commit()
    # A small number of captured process records contain only the run accession.
    # Preserve that fact in process_missing, and use the canonical record solely
    # as a fallback for downstream calculations and display.
    con.execute("""UPDATE executed SET
      scientific_name=coalesce(scientific_name,(SELECT scientific_name FROM ena WHERE ena.run=executed.run)),
      base_count=coalesce(base_count,(SELECT base_count FROM ena WHERE ena.run=executed.run)),
      read_count=coalesce(read_count,(SELECT read_count FROM ena WHERE ena.run=executed.run))
    """)
    con.commit()
    incomplete = con.execute("SELECT count(*) FROM executed WHERE status IS NULL OR process_seen=0 OR scientific_name IS NULL OR base_count IS NULL OR read_count IS NULL").fetchone()[0]
    if incomplete:
        raise AssemblyBuildError(f"{incomplete} executed rows lack status or process-time metadata")


def _load_aux(con: sqlite3.Connection, inputs: AssemblyInputs) -> dict[str, int]:
    sizes: dict[str, int] = {}
    _require(inputs.genome_sizes, {"accession", "genome_size"})
    for n,row in iter_tsv_dicts(inputs.genome_sizes):
        if row["accession"] in sizes:
            raise AssemblyBuildError(f"{inputs.genome_sizes}:{n}: duplicate accession")
        sizes[row["accession"]] = _int(row["genome_size"], f"{inputs.genome_sizes}:{n}")

    _require(inputs.sylph, {"Sample","Run","Genome_file","Sequence_abundance","Eff_cov","Species"})
    con.execute("BEGIN")
    for n,row in iter_tsv_dicts(inputs.sylph):
        genome = row["Genome_file"].split("/")[-1].rsplit("_",1)[0]
        if genome not in sizes:
            raise AssemblyBuildError(f"{inputs.sylph}:{n}: no genome size for {genome}")
        expected = con.execute("SELECT run FROM executed WHERE sample=?", (row["Sample"],)).fetchone()
        if expected is None or expected[0] != row["Run"]:
            raise AssemblyBuildError(f"{inputs.sylph}:{n}: sample/run not in executed list")
        _insert_unique(con, "INSERT INTO sylph VALUES(?,?,?,?,?,?)", (
            row["Sample"],row["Species"],row["Run"],_float(row["Sequence_abundance"],f"{inputs.sylph}:{n}"),
            _float(row["Eff_cov"],f"{inputs.sylph}:{n}"),genome
        ), f"{inputs.sylph}:{n}")
    con.commit()

    con.execute("BEGIN")
    with open_text(inputs.sylph_no_matches) as handle:
        for n,line in enumerate(handle,1):
            sample=line.rstrip("\n")
            if con.execute("SELECT 1 FROM executed WHERE sample=?",(sample,)).fetchone() is None:
                raise AssemblyBuildError(f"{inputs.sylph_no_matches}:{n}: unexpected sample")
            _insert_unique(con,"INSERT INTO no_match VALUES(?)",(sample,),f"{inputs.sylph_no_matches}:{n}")
    con.commit()

    _require(inputs.assembly_stats,{"sample","total_length","number","N50"})
    con.execute("BEGIN")
    for n,row in iter_tsv_dicts(inputs.assembly_stats):
        _insert_unique(con,"INSERT INTO stats VALUES(?,?,?,?)",(
            row["sample"],_int(row["total_length"],f"{inputs.assembly_stats}:{n}"),
            _int(row["number"],f"{inputs.assembly_stats}:{n}"),_int(row["N50"],f"{inputs.assembly_stats}:{n}")
        ),f"{inputs.assembly_stats}:{n}")
    con.commit()

    _require(inputs.checkm2,{"Name","Completeness_General","Contamination","Additional_Notes"})
    con.execute("BEGIN")
    for n,row in iter_tsv_dicts(inputs.checkm2):
        _insert_unique(con,"INSERT INTO checkm VALUES(?,?,?,?)",(
            row["Name"],row["Completeness_General"],row["Contamination"],row["Additional_Notes"]
        ),f"{inputs.checkm2}:{n}")
    con.commit()

    con.execute("BEGIN")
    with open_text(inputs.seqkit) as handle:
        for n,line in enumerate(handle,1):
            fields=line.rstrip("\n").split("\t")
            if len(fields)!=2:
                raise AssemblyBuildError(f"{inputs.seqkit}:{n}: expected two fields")
            _insert_unique(con,"INSERT INTO seqkit VALUES(?,?)",tuple(fields),f"{inputs.seqkit}:{n}")
    con.commit()

    counts={t:con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("executed","sylph","no_match","stats","checkm","seqkit")}
    for table in ("stats","checkm","seqkit"):
        bad=con.execute(f"SELECT count(*) FROM {table} t LEFT JOIN executed e ON t.sample=e.sample WHERE e.sample IS NULL").fetchone()[0]
        if bad: raise AssemblyBuildError(f"{bad} {table} rows are not executed samples")
    if counts["stats"] != counts["checkm"]:
        raise AssemblyBuildError("assembly-stats and checkm2 sample counts differ")
    mismatch=con.execute("SELECT count(*) FROM stats LEFT JOIN checkm USING(sample) WHERE checkm.sample IS NULL").fetchone()[0]
    if mismatch: raise AssemblyBuildError(f"{mismatch} assembly-stats samples lack checkm2")
    no_result=con.execute("""SELECT count(*) FROM executed e LEFT JOIN sylph s USING(sample)
      LEFT JOIN no_match n USING(sample) WHERE s.sample IS NULL AND n.sample IS NULL""").fetchone()[0]
    overlap=con.execute("SELECT count(DISTINCT s.sample) FROM sylph s JOIN no_match n USING(sample)").fetchone()[0]
    if no_result or overlap:
        raise AssemblyBuildError(f"Sylph census mismatch: {no_result} unclassified, {overlap} overlap")
    return {**counts,"genome_sizes":len(sizes),"_sizes":sizes}  # type: ignore[dict-item]


def _metadata_filters(con: sqlite3.Connection, sample: str, used_runs: list[str], dataset: str,
                      historical_unknown_runs: set[str] | None = None) -> set[str]:
    reasons: set[str] = set()
    passing=con.execute("SELECT count(*) FROM run_sample rs JOIN run r USING(run) WHERE rs.sample=? AND r.pass=1",(sample,)).fetchone()[0]
    if passing == 0: reasons.add("NO_RUNS")
    for run_name in used_runs:
        row=con.execute("SELECT r.*,e.current_samples FROM run r JOIN ena e USING(run) WHERE run=?",(run_name,)).fetchone()
        if row is None:
            if historical_unknown_runs is None:
                raise AssemblyBuildError(f"assembly {sample}: unknown used run {run_name}")
            # Some already-documented, long-removed assembly-provenance runs
            # predate every retained ENA source and the previous run table.
            historical_unknown_runs.add(run_name)
            reasons.add("RUN_REMOVED")
            continue
        if not row["present"]: reasons.add("RUN_REMOVED")
        if len(_split(row["samples"])) != 1: reasons.add("RMMS")
        if row["present"] and sample not in _split(row["current_samples"]): reasons.add("RUN_CHANGE")
        if dataset == "661k" and not row["meta_atb"]: reasons.add("META_FAIL")
    return reasons


def _sylph_call(con: sqlite3.Connection, sample: str, sizes: dict[str,int]) -> tuple[str,str]:
    execution=con.execute("SELECT run,status,base_count,read_count FROM executed WHERE sample=?",(sample,)).fetchone()
    rows=con.execute("SELECT * FROM sylph WHERE sample=? ORDER BY species",(sample,)).fetchall()
    if not rows:
        if execution["status"] in {"downloaded_reads_fail","reads_fastq_gzip_check_fail"}:
            return "NA","NA"
        return "unknown","NO_SYLPH_RESULTS"
    passed=[]
    base_count=_int(execution["base_count"],f"{sample}:base_count")
    read_count=_int(execution["read_count"],f"{sample}:read_count")
    for row in rows:
        failed=row["sequence_abundance"] < 99
        if read_count <= 0:
            failed=True
        else:
            read_length=base_count/(read_count*2)
            denominator=read_length-31+1
            if denominator <= 0 or row["eff_cov"]*sizes[row["genome_accession"]]*read_length/denominator < 0.5*base_count:
                failed=True
        if not failed: passed.append(row["species"])
    if len(passed)>1:
        return ";".join(passed),"MULTIPLE"
    return (passed[0],"PASS") if passed else ("unknown","SYLPH_RESULTS_FAIL")


def _hq_filter(metadata: set[str], sylph_filter: str, stats: sqlite3.Row | None,
               checkm: sqlite3.Row | None, extra_reasons: set[str] | None = None) -> str:
    extra_reasons = extra_reasons or set()
    if stats is None or checkm is None:
        return _format_filters(extra_reasons) if extra_reasons else "NA"
    reasons=set(metadata) | extra_reasons
    if stats["total_length"] < 100_000: reasons.add("ASM_MIN_LEN")
    if stats["number"] > 2_000: reasons.add("MAX_CONTIG_NUM")
    if stats["n50"] < 2_000: reasons.add("MIN_N50")
    if checkm["completeness"] in {"", "NA"} or checkm["contamination"] in {"", "NA"}:
        reasons.add("CHECKM2_NO_DIAMOND")
    else:
        if _float(checkm["completeness"],"checkm completeness") < 90: reasons.add("CHECKM2_MIN_COMPL")
        if _float(checkm["contamination"],"checkm contamination") > 5: reasons.add("CHECKM2_MAX_CONTAM")
    if sylph_filter != "PASS": reasons.add(sylph_filter)
    return _format_filters(reasons)


def _natural_accession(value: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"([A-Za-z]+)([0-9]+)", value)
    return (match.group(1), int(match.group(2)), value) if match else (value, -1, value)


def _first_public_for_runs(con: sqlite3.Connection, runs: list[str]) -> str:
    dates = [row[0] for run in runs if (row := con.execute(
        "SELECT first_public FROM ena WHERE run=?", (run,)).fetchone()) and row[0] not in {"", "NA"}]
    return max(dates, default="")


def _append_comment(existing: str, addition: str) -> str:
    return addition if existing in {"", "None", "NA"} else f"{existing}; {addition}"


def _duplicate_note(status: str, members: str) -> str:
    return (
        f"Global Seqkit duplicate assessment: {status} (keeper selected by earliest first_public then sample_accession); "
        f"matching duplicate sample/run accessions: {members}"
    )


def _duplicate_assignments(con: sqlite3.Connection, inputs: AssemblyInputs) -> dict[str, int]:
    """Assign every non-empty global seqkit duplicate, retaining one deterministic keeper."""
    con.execute("BEGIN")
    for _, row in iter_tsv_dicts(inputs.previous_assembly):
        value = row["assembly_seqkit_sum"]
        if value not in {"NA", "", EMPTY_SEQKIT_SUM}:
            _insert_unique(con, "INSERT INTO seqkit_member VALUES(?,?,?)", (value, row["sample_accession"], row["run_accession"]), f"{inputs.previous_assembly}")
    for sample, run, value in con.execute("SELECT e.sample,e.run,s.value FROM executed e JOIN seqkit s USING(sample) WHERE s.value<>?", (EMPTY_SEQKIT_SUM,)):
        _insert_unique(con, "INSERT INTO seqkit_member VALUES(?,?,?)", (value, sample, run), "new executed seqkit")
    con.commit()

    groups = rows = keepers = likely = 0
    for (value,) in con.execute("SELECT seqkit_sum FROM seqkit_member GROUP BY seqkit_sum HAVING count(*)>1 ORDER BY seqkit_sum"):
        members = con.execute("SELECT sample,run FROM seqkit_member WHERE seqkit_sum=? ORDER BY sample", (value,)).fetchall()
        # Missing publication dates sort after known dates. This preserves a
        # stable earliest-public record whenever ENA provides the information.
        dated_members = [
            (member, _first_public_for_runs(con, _split_runs(member["run"])))
            for member in members
        ]
        keeper, _ = min(
            dated_members,
            key=lambda item: ((0, item[1]) if item[1] else (1, ""), _natural_accession(item[0]["sample"])),
        )
        rendered = {member["sample"]: f"{member['sample']}/{member['run']}" for member in members}
        con.execute("BEGIN")
        for member in members:
            status = "KEEP_OLDEST" if member["sample"] == keeper["sample"] else "LIKELY_DUPLICATE"
            peers = ",".join(rendered[other["sample"]] for other in members if other["sample"] != member["sample"])
            _insert_unique(con, "INSERT INTO duplicate_assignment VALUES(?,?,?)", (member["sample"], status, peers), "duplicate assignment")
            keepers += status == "KEEP_OLDEST"; likely += status == "LIKELY_DUPLICATE"
        con.commit()
        groups += 1; rows += len(members)
    return {"duplicate_seqkit_group_count": groups, "duplicate_seqkit_rows": rows,
            "duplicate_seqkit_keeper_rows": keepers, "duplicate_seqkit_likely_duplicate_rows": likely}


def _duplicate_assignment(con: sqlite3.Connection, sample: str) -> tuple[str, str]:
    row = con.execute("SELECT status,matching_members FROM duplicate_assignment WHERE sample=?", (sample,)).fetchone()
    return (row["status"], row["matching_members"]) if row else ("NONE", "")


def _legacy_in_release_1(dataset: str, asm_fasta_on_osf: str, duplicate_status: str) -> str:
    """Release 1 retains published legacy artifacts except duplicate non-keepers."""
    return str(int(
        dataset in RELEASE_1_LEGACY_DATASETS
        and asm_fasta_on_osf == "1"
        and duplicate_status != "LIKELY_DUPLICATE"
    ))


def _refresh_old(con: sqlite3.Connection,row: dict[str,str],historical_unknown_runs: set[str]) -> dict[str,str]:
    used_runs=_split_runs(row["run_accession"])
    unknown=any(con.execute("SELECT 1 FROM run WHERE run=?",(run,)).fetchone() is None for run in used_runs)
    metadata=_metadata_filters(con,row["sample_accession"],used_runs,row["dataset"],historical_unknown_runs)
    if unknown:
        metadata |= _filters(row["asm_pipe_filter"]) & METADATA_FILTERS
    base=_filters(row["asm_pipe_filter"])-METADATA_FILTERS
    status, peers = _duplicate_assignment(con, row["sample_accession"])
    duplicate_reason = {DUP_SEQKIT_SUM} if status == "LIKELY_DUPLICATE" else set()
    row["asm_pipe_filter"]=_format_filters(base|metadata|duplicate_reason)
    if row["hq_filter"] != "NA":
        row["hq_filter"]=_format_filters((_filters(row["hq_filter"])-METADATA_FILTERS)|metadata|duplicate_reason)
    elif duplicate_reason:
        row["hq_filter"] = DUP_SEQKIT_SUM
    row["duplicate_seqkit_sum_status"] = status
    if status != "NONE": row["comments"] = _append_comment(row["comments"], _duplicate_note(status, peers))
    row["in_release_1"] = _legacy_in_release_1(row["dataset"], row["asm_fasta_on_osf"], status)
    return row


def _new_processed(con: sqlite3.Connection,sample: str,sizes: dict[str,int],release: str) -> tuple[dict[str,str],list[str]]:
    ex=con.execute("SELECT * FROM executed WHERE sample=?",(sample,)).fetchone()
    used=[ex["run"]]
    dataset=f"Incr_release.{release}"
    metadata=_metadata_filters(con,sample,used,dataset)
    stats=con.execute("SELECT * FROM stats WHERE sample=?",(sample,)).fetchone()
    checkm=con.execute("SELECT * FROM checkm WHERE sample=?",(sample,)).fetchone()
    seqkit=con.execute("SELECT value FROM seqkit WHERE sample=?",(sample,)).fetchone()
    seqkit_value = seqkit[0] if seqkit else "NA"
    duplicate_status, duplicate_peers = _duplicate_assignment(con, sample)
    pipeline=_filters(PIPELINE_STATUS[ex["status"]])|metadata
    duplicate_reason = {DUP_SEQKIT_SUM} if duplicate_status == "LIKELY_DUPLICATE" else set()
    pipeline |= duplicate_reason
    if ex["status"]=="finished" and (stats is None or not 100_000 <= stats["total_length"] <= 15_000_000):
        pipeline.add("ASM_LEN")
    species,sylph_filter=_sylph_call(con,sample,sizes)
    mismatch=[]
    latest=con.execute("SELECT * FROM ena WHERE run=?",(ex["run"],)).fetchone()
    unavailable=set(ex["process_missing"].split(",")) if ex["process_missing"] else set()
    for key in ("fastq_md5","fastq_bytes","base_count","read_count"):
        if key in unavailable: mismatch.append("PROCESS_METADATA_MISSING_"+key)
        elif ex[key] != latest[key]: mismatch.append(key)
    if "sample_accession" in unavailable: mismatch.append("PROCESS_METADATA_MISSING_sample_accession")
    elif sample not in _split(latest["current_samples"]): mismatch.append("sample_accession")
    process_change_filters = {PROCESS_METADATA_CHANGE_FILTERS[key] for key in mismatch if key in PROCESS_METADATA_CHANGE_FILTERS}
    pipeline |= process_change_filters
    comments="None" if not mismatch else f"{release} process-time/current ENA mismatch: "+",".join(sorted(mismatch))
    if process_change_filters:
        comments = _append_comment(comments, "applied filters: "+",".join(sorted(process_change_filters)))
    if duplicate_status != "NONE": comments = _append_comment(comments, _duplicate_note(duplicate_status, duplicate_peers))
    result={
      "sample_accession":sample,"run_accession":ex["run"],"assembly_accession":"TODO",
      "assembly_seqkit_sum":seqkit_value,"duplicate_seqkit_sum_status":duplicate_status,"asm_pipe_filter":_format_filters(pipeline),
      "asm_fasta_on_osf":"0","dataset":dataset,"scientific_name":ex["scientific_name"],
      "sylph_species_pre_202505":"NA","in_hq_pre_202505":"NA","sylph_species":species,
      "sylph_filter":sylph_filter,"hq_filter":_hq_filter(metadata,sylph_filter,stats,checkm,duplicate_reason|process_change_filters),
      "osf_tarball_filename":"TODO","osf_tarball_url":"TODO","aws_url":"TODO","comments":comments,
    }
    result["in_release_1"] = str(int(not pipeline))
    return result,mismatch


def build_assembly_table(inputs: AssemblyInputs, paths: AssemblyBuildPaths) -> dict[str,Any]:
    for output in (paths.output,paths.report):
        if output.exists(): raise AssemblyBuildError(f"refusing to overwrite output: {output}")
        output.parent.mkdir(parents=True,exist_ok=True)
    con=_new_db(paths.database)
    try:
        _load_core(con,inputs); _load_execution(con,inputs)
        aux=_load_aux(con,inputs); sizes=aux.pop("_sizes")
        duplicate_counts = _duplicate_assignments(con, inputs)
        old_count=processed_count=placeholder_count=duplicate_seqkit_count=0
        historical_unknown_runs: set[str] = set()
        asm_counts=Counter(); hq_counts=Counter(); mismatch_counts=Counter()
        with open_tsv_writer(paths.output) as writer:
            writer.writerow(ASSEMBLY_COLUMNS)
            for _,row in iter_tsv_dicts(inputs.previous_assembly):
                result=_refresh_old(con,row,historical_unknown_runs)
                writer.writerow([result[c] for c in ASSEMBLY_COLUMNS]); old_count+=1
                asm_counts[result["asm_pipe_filter"]]+=1; hq_counts[result["hq_filter"]]+=1
            for (sample,) in con.execute("SELECT sample FROM executed ORDER BY sample"):
                result,mismatches=_new_processed(con,sample,sizes,inputs.release)
                writer.writerow([result[c] for c in ASSEMBLY_COLUMNS]); processed_count+=1
                asm_counts[result["asm_pipe_filter"]]+=1; hq_counts[result["hq_filter"]]+=1; mismatch_counts.update(mismatches)
                duplicate_seqkit_count += int(DUP_SEQKIT_SUM in result["asm_pipe_filter"].split(","))
            query="""SELECT rs.sample,group_concat(rs.run),group_concat(e.scientific_name, char(31))
              FROM run_sample rs JOIN ena e USING(run) LEFT JOIN old_sample o ON rs.sample=o.sample
              LEFT JOIN executed x ON rs.sample=x.sample WHERE o.sample IS NULL AND x.sample IS NULL
              GROUP BY rs.sample ORDER BY rs.sample"""
            for sample,runs,names in con.execute(query):
                used=sorted(runs.split(",")); metadata=_metadata_filters(con,sample,used,"not_processed")
                result={c:"NA" for c in ASSEMBLY_COLUMNS}
                result.update({"sample_accession":sample,"run_accession":",".join(used),"assembly_accession":"TODO",
                  "assembly_seqkit_sum":"NA","asm_pipe_filter":_format_filters({"NOT_PROCESSED"}|metadata),
                  "duplicate_seqkit_sum_status":"NONE",
                  "in_release_1":"0",
                  "asm_fasta_on_osf":"0","dataset":"not_processed","scientific_name":";".join(sorted(set(names.split(chr(31))))) if names else "",
                  "sylph_species_pre_202505":"NA","in_hq_pre_202505":"NA","sylph_species":"NA","sylph_filter":"NA","hq_filter":"NA",
                  "osf_tarball_filename":"TODO","osf_tarball_url":"TODO","aws_url":"TODO","comments":"None"})
                writer.writerow([result[c] for c in ASSEMBLY_COLUMNS]); placeholder_count+=1
                asm_counts[result["asm_pipe_filter"]]+=1; hq_counts["NA"]+=1
    finally:
        con.close()
    report={"status":"PASS","previous_rows":old_count,"new_processed_rows":processed_count,
      "new_not_processed_rows":placeholder_count,"output_rows":old_count+processed_count+placeholder_count,
      "new_likely_duplicate_seqkit_rows":duplicate_seqkit_count,
      **duplicate_counts,
      "historical_assembly_runs_absent_from_run_table":sorted(historical_unknown_runs),
      "historical_assembly_runs_absent_from_run_table_count":len(historical_unknown_runs),
      "auxiliary_counts":aux,"process_time_current_mismatch_counts":dict(sorted(mismatch_counts.items())),
      "asm_pipe_filter_counts":dict(sorted(asm_counts.items())),"hq_filter_counts":dict(sorted(hq_counts.items())),
      "policy":{"release":inputs.release,"current_snapshot_column":inputs.current_snapshot_column},
      "inputs":{k:{"path":str(v.resolve()),"sha256":sha256_file(v)} for k,v in vars(inputs).items() if isinstance(v,Path)},
      "output":{"path":str(paths.output.resolve()),"sha256":sha256_file(paths.output),"columns":list(ASSEMBLY_COLUMNS)}}
    paths.report.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    return report
