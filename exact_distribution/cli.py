from __future__ import annotations
import argparse
from pathlib import Path
from .builder import build_candidate

def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(prog="exactpack")
    parser.add_argument("source"); parser.add_argument("--output",required=True)
    parser.add_argument("--work",required=True); parser.add_argument("--engine-root",required=True)
    args=parser.parse_args(argv)
    def progress(phase,index,total,path): print(f"EXACT_PROGRESS: {phase}|{index}|{total}|{path}",flush=True)
    report=build_candidate(Path(args.source),Path(args.output),Path(args.work),Path(args.engine_root),progress)
    print(f"EXACT_REPORT: {report['report_path']}")
    print(f"EXACT_OUTPUT: {Path(report['candidate']).absolute()}")
    return 0
