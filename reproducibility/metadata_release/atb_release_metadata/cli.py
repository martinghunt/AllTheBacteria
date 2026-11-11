from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .assembly_plan import AssemblyPlanPaths, make_assembly_plan
from .assembly_table import AssemblyBuildPaths, AssemblyInputs, build_assembly_table
from .historical_gaps import export_historical_assembly_run_gaps
from .duplicate_assemblies import export_duplicate_assembly_report
from .duplicate_metadata_comparison import compare_duplicate_ena_metadata
from .ena_latest import BuildPaths, build
from .run_table import RunBuildPaths, build_run_table
from .release_manifest import export_sample_run_base_count
from .validate_661k import validate as validate_661k


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atb-release-metadata",
        description="Build reproducible AllTheBacteria release metadata",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    ena = subparsers.add_parser(
        "build-ena-latest",
        help="merge ENA observations into a latest-known record per run",
    )
    ena.add_argument("--config", required=True, type=Path)
    ena.add_argument("--work-db", required=True, type=Path)
    ena.add_argument("--output", required=True, type=Path)
    ena.add_argument("--changes", required=True, type=Path)
    ena.add_argument("--manifest", required=True, type=Path)

    old = subparsers.add_parser(
        "validate-661k",
        help="reconcile the published 661k JSON, sample files, and normalized ENA table",
    )
    old.add_argument("--json", required=True, type=Path)
    old.add_argument("--file3", required=True, type=Path)
    old.add_argument("--file9", required=True, type=Path)
    old.add_argument("--ena-table", required=True, type=Path)
    old.add_argument("--report", required=True, type=Path)

    run = subparsers.add_parser(
        "build-run-table",
        help="update the release run table from previous run and ena_latest",
    )
    run.add_argument("--previous-run", required=True, type=Path)
    run.add_argument("--ena-latest", required=True, type=Path)
    run.add_argument("--work-db", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--report", required=True, type=Path)
    run.add_argument("--release", default="202603")
    run.add_argument("--current-snapshot-column", default="in_ena_20260317")

    plan = subparsers.add_parser(
        "plan-assemblies",
        help="derive the clean-slate one-run-per-sample assembly candidate set",
    )
    plan.add_argument("--previous-assembly", required=True, type=Path)
    plan.add_argument("--run-table", required=True, type=Path)
    plan.add_argument("--executed", type=Path)
    plan.add_argument("--work-db", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    plan.add_argument("--report", required=True, type=Path)

    assembly = subparsers.add_parser(
        "build-assembly-table",
        help="refresh old assembly filters and add processed and unprocessed new samples",
    )
    for option in (
        "previous-assembly", "run-table", "ena-latest", "executed",
        "process-metadata", "status", "sylph", "sylph-no-matches",
        "genome-sizes", "assembly-stats", "checkm2", "seqkit",
        "work-db", "output", "report",
    ):
        assembly.add_argument(f"--{option}", required=True, type=Path)
    assembly.add_argument("--release", default="202603")
    assembly.add_argument("--current-snapshot-column", default="in_ena_20260317")

    gaps = subparsers.add_parser(
        "export-historical-assembly-run-gaps",
        help="write sample/run mappings for assembly-provenance runs absent from ena_latest",
    )
    gaps.add_argument("--assembly", required=True, type=Path)
    gaps.add_argument("--assembly-report", required=True, type=Path)
    gaps.add_argument("--output", required=True, type=Path)

    duplicates = subparsers.add_parser(
        "export-duplicate-assembly-report",
        help="report non-empty duplicate seqkit assembly sums without collapsing rows",
    )
    duplicates.add_argument("--assembly", required=True, type=Path)
    duplicates.add_argument("--ena-latest", required=True, type=Path)
    duplicates.add_argument("--dataset", required=True)
    duplicates.add_argument("--output", required=True, type=Path)

    compare = subparsers.add_parser(
        "compare-duplicate-ena-metadata",
        help="summarise substantive ENA metadata differences in duplicate assembly groups",
    )
    compare.add_argument("--duplicate-report", required=True, type=Path)
    compare.add_argument("--ena-metadata", required=True, type=Path)
    compare.add_argument("--output", required=True, type=Path)

    manifest = subparsers.add_parser(
        "export-sample-run-base-count",
        help="export one pipeline-pass release dataset with canonical ENA base counts",
    )
    manifest.add_argument("--assembly", required=True, type=Path)
    manifest.add_argument("--ena-latest", required=True, type=Path)
    manifest.add_argument("--dataset", required=True)
    manifest.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "build-ena-latest":
        result = build(
            load_config(args.config),
            BuildPaths(
                database=args.work_db,
                output=args.output,
                changes=args.changes,
                manifest=args.manifest,
            ),
        )
        print(json.dumps(result["outputs"], indent=2, sort_keys=True))
    elif args.command == "validate-661k":
        result = validate_661k(
            args.json, args.file3, args.file9, args.ena_table, args.report
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "build-run-table":
        result = build_run_table(
            args.previous_run,
            args.ena_latest,
            RunBuildPaths(args.work_db, args.output, args.report),
            args.current_snapshot_column,
            args.release,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "plan-assemblies":
        result = make_assembly_plan(
            args.previous_assembly,
            args.run_table,
            AssemblyPlanPaths(args.work_db, args.output, args.report),
            args.executed,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "build-assembly-table":
        result = build_assembly_table(
            AssemblyInputs(
                previous_assembly=args.previous_assembly, run_table=args.run_table,
                ena_latest=args.ena_latest, executed=args.executed,
                process_metadata=args.process_metadata, status=args.status,
                sylph=args.sylph, sylph_no_matches=args.sylph_no_matches,
                genome_sizes=args.genome_sizes, assembly_stats=args.assembly_stats,
                checkm2=args.checkm2, seqkit=args.seqkit, release=args.release,
                current_snapshot_column=args.current_snapshot_column,
            ),
            AssemblyBuildPaths(args.work_db, args.output, args.report),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "export-historical-assembly-run-gaps":
        result = export_historical_assembly_run_gaps(
            args.assembly, args.assembly_report, args.output
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "export-duplicate-assembly-report":
        result = export_duplicate_assembly_report(
            args.assembly, args.ena_latest, args.output, args.dataset
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "compare-duplicate-ena-metadata":
        result = compare_duplicate_ena_metadata(
            args.duplicate_report, args.ena_metadata, args.output
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "export-sample-run-base-count":
        result = export_sample_run_base_count(
            args.assembly, args.ena_latest, args.output, args.dataset
        )
        print(json.dumps(result, indent=2, sort_keys=True))
