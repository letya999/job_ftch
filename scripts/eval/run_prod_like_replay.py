from __future__ import annotations

import asyncio
import sys

from scripts.eval.run_pipeline_eval import main as _run_pipeline_eval_main


def _ensure_flag(argv: list[str], flag: str, value: str) -> list[str]:
    if flag in argv:
        return argv
    return [argv[0], flag, value, *argv[1:]]


if __name__ == "__main__":
    sys.argv = _ensure_flag(sys.argv, "--state-mode", "runtime")
    raise SystemExit(asyncio.run(_run_pipeline_eval_main()))
