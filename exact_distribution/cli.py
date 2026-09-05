from __future__ import annotations
import argparse
import sys
from pathlib import Path
from .builder import build_candidate

def main(argv: list[str] | None = None) -> int:
    # WPF reads redirected worker output as UTF-8, including native Unicode paths.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser=argparse.ArgumentParser(prog="exactpack")
    parser.add_argument("source"); parser.add_argument("--output",required=True)
    parser.add_argument("--work",required=True); parser.add_argument("--engine-root",required=True)
    args=parser.parse_args(argv)
    def progress(phase,index,total,path): print(f"EXACT_PROGRESS: {phase}|{index}|{total}|{path}",flush=True)
    report=build_candidate(Path(args.source),Path(args.output),Path(args.work),Path(args.engine_root),progress)
    print(f"EXACT_REPORT: {report['report_path']}")
    print(f"EXACT_OUTPUT: {Path(report['candidate']).absolute()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
