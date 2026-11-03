"""Compatibility entry point for the packaged classification harness."""

from job_ftch.eval.classification import evaluate as evaluate
from job_ftch.eval.classification import main

if __name__ == "__main__":
    raise SystemExit(main())
