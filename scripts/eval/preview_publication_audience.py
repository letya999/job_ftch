"""Preview a recipient policy on canonical JobRecord JSONL without contacting any sink."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import TYPE_CHECKING

from job_ftch.application.builder import load_tenant_config
from job_ftch.application.publication_audience import evaluate_audience, publication_facts
from job_ftch.domain import JobRecord

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TextIO

    from job_ftch.domain.publication_policy import PublicationPolicy


def preview(lines: Iterable[str], policy: PublicationPolicy, output: TextIO) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for line in lines:
        if not line.strip():
            continue
        job = JobRecord.model_validate_json(line)
        decision = evaluate_audience(job, policy)
        counts[decision.status] += 1
        print(
            json.dumps(
                {
                    "id": job.group_id or job.job_id or job.raw_item_id,
                    "source": job.source_name,
                    "url": str(job.canonical_url or ""),
                    "policy_version": policy.version,
                    "decision": decision.status,
                    "reasons": decision.reasons,
                    "evidence": decision.evidence,
                    "tags": publication_facts(job).hashtags(),
                },
                ensure_ascii=False,
            ),
            file=output,
        )
    return dict(counts)


def main() -> None:
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-config", required=True, type=Path)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    tenant = load_tenant_config(args.tenant_config)
    policy = tenant.publication_policies.get(args.target, tenant.publication_policies.get("*"))
    if policy is None:
        parser.error(f"No audience configured for {args.target}")
    print(json.dumps(preview(sys.stdin, policy, sys.stdout)), file=sys.stderr)


if __name__ == "__main__":
    main()
