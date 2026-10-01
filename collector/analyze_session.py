"""Command line entry point for Phase 2 offline telemetry analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from analysis.build import build_analysis
from analysis.incremental import update_analysis


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a flashback-safe, distance-normalized F1 analysis data set"
    )
    parser.add_argument("session", type=Path, help="Session directory or raw_packets.bin")
    parser.add_argument(
        "--output", type=Path, help="Output directory (default: SESSION/analysis_v0.10.0; full/export uses legacy engine)"
    )
    parser.add_argument(
        "--distance-step", type=float, default=5.0,
        help="Distance normalization interval in metres (default: 5)",
    )
    parser.add_argument(
        "--include-invalid", action="store_true",
        help="Also normalize laps marked invalid by the game",
    )
    parser.add_argument("--force-rebuild", action="store_true", help="Recompute derived results from reusable foundation data")
    parser.add_argument("--export", action="store_true", help="Also generate CSV exports (disabled by default to avoid duplicate storage)")
    parser.add_argument("--recover-tail", action="store_true", help="Explicitly analyze only a verified prefix when the final Raw record/block is truncated")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not (args.include_invalid or args.force_rebuild or args.export):
            summary = update_analysis(args.session, output=args.output, distance_step_m=args.distance_step,
                                      recover_tail=args.recover_tail)
        else:
            summary = build_analysis(
                args.session,
                output=args.output,
                distance_step_m=args.distance_step,
                include_invalid=args.include_invalid,
                force_rebuild=args.force_rebuild,
                export_files=args.export,
                recover_tail=args.recover_tail,
            )
    except Exception as exc:
        print(f"Analysis failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["decode_error_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
