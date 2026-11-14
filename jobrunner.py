"""
python jobrunner.py <job> --input-dir D --output-dir D --tmp-dir D [--now ISO] [--ref REF]
"""

import argparse
import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

from jobcontext import DEFAULT_REF, JobContext
from jobs import JOBS


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("job", choices=sorted(JOBS))
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--tmp-dir", type=Path, required=True)
    p.add_argument("--now", type=datetime.fromisoformat, default=None)
    p.add_argument("--ref", default=DEFAULT_REF)
    args = p.parse_args(argv)

    job = JOBS[args.job]
    module_name, func_name = job.target.split(":")
    func = getattr(importlib.import_module(module_name), func_name)

    ctx = JobContext(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        tmp_dir=args.tmp_dir,
        now=args.now or datetime.now(tz=timezone.utc),
        ref=args.ref,
    )
    print(f"[{job.name}] start now={ctx.now.isoformat()} ref={ctx.ref}", flush=True)
    func(ctx)
    print(f"[{job.name}] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
