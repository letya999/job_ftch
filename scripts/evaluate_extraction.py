"""Compatibility entry point for the packaged extraction harness."""

import asyncio

from job_ftch.eval.extraction import _main
from job_ftch.eval.extraction import evaluate_fixture as evaluate_fixture

if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
