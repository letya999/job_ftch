"""Apply explicit manual fixes for obvious target-role mislabels in eval_dataset."""

from __future__ import annotations

import json
from pathlib import Path

DATASET = Path("fixtures/dataset/eval_dataset.jsonl")

FIXES: dict[str, str] = {
    "5853644b5fcfe2fd5d25f69582305e0d00f276eb49e74544be12744e12ac62ef": (
        "Senior LLM developer role building GPT/RAG products; new label was a 429 crash."
    ),
    "6270690d6685ca9a9999063b63cae1ce29f5963d8099682d08da1bcf0bda35b2": (
        "Backend Python engineer in AI-agents team with RAG/LLM integrations; new label was a 429 crash."
    ),
    "41526606d9501bc2b20ce2d4ccd25c1b1f340685e333aef4da13ff1df908c437": (
        "Senior AI Agent Engineer vacancy; new label was a 429 crash."
    ),
    "38ce8246b19350f4ed08dafe0ff13c9da67332ada6d620ef917f5e902a2bdfde": (
        "AI Engineer role applying OpenAI API, LangChain and prompt engineering; new label was a 429 crash."
    ),
    "158b91d12352e9c342be601b8e85ac0b95d1e2815c569594e420dd35d661253c": (
        "AI Engineer vacancy; title and body are target-role aligned."
    ),
    "a612310f1d7a94ea589e2b1527fb17abd14db868c0ed42147298a11b3c3fc3a7": (
        "AI Engineer vacancy; title and body are target-role aligned."
    ),
    "c5536e772956ec2bd011b3ed2d2d743c4be1d0fd3d7d914b3c00927b5d1be9e8": (
        "AI Engineer vacancy; title and body are target-role aligned."
    ),
    "f6ee29861be94680def0e3592bf696d3f5941cfc21561661dd904af3b84f1955": (
        "AI Engineer vacancy with OpenAI/LangChain/LangGraph and prompt engineering; new label was a 429 crash."
    ),
    "a04f0822707095c6ad9bcb529b6f49b317f2f82ce9bbbf0e18d6504374c83646": (
        "AI Engineer vacancy with Yandex AI Studio, RAG, Qdrant and LLM integrations; new label was a 429 crash."
    ),
    "419a2388b523072a7e864de9ed79a28a27e4cede7d07612188789d6d7c0bb3ad": (
        "Prompt Engineer is in target applied-LLM scope."
    ),
    "e333af554016e25e2466861bd2950f215e3bf01379ac2547e0788fe5522f3488": (
        "Senior Prompt Engineer role with LLM, LangChain, APIs and automation is in target applied-LLM scope."
    ),
    "0d552785bae9d9cbc008f476a5a22d602eaae1650577aa55b5cf7fe3aa66bbbc": (
        "Vibe coder is in target scope per product relevance definition."
    ),
    "15f89f564fe59b34077c00d4e1b79ec0741410ae3ef46d44005021c64acd48f2": (
        "ML Engineer role includes production LLM/RAG pipelines, LangChain/LangGraph, "
        "agent frameworks and integration of AI solutions into existing infrastructure."
    ),
    "afe919dca763f6e830e9e75c2b6372b46ce5ade11b28f84ad28097022cdc1bdc": (
        "DS-titled role is actually AI-agent product engineering: text2json/text2sql, "
        "copilot, multi-agent system and production agent runtime integration."
    ),
    "cbb98da77c359cf143f080222990964becdbc7c3782190502375dbd25ee22a07": (
        "Python developer role building GPT/LLM projects, LangChain chains, "
        "AI assistants and business-process automation."
    ),
    "c41a825df2dde408df52635b3b5455ac2d70715b2524d41dbbcd9178d66cce04": (
        "ML Engineer role includes backend ML services, LLM integration, RAG, "
        "vector search and AI/ML features in applications."
    ),
}


def main() -> None:
    if not DATASET.exists():
        raise SystemExit(f"Dataset not found: {DATASET}")

    rows = []
    touched = 0
    seen: set[str] = set()
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        stable_id = str(row.get("stable_id", ""))
        reason = FIXES.get(stable_id)
        if reason is not None:
            row["relevant"] = 1
            row["labeler"] = "manual-target-fix"
            row["reason"] = reason
            touched += 1
            seen.add(stable_id)
        rows.append(row)

    missing = sorted(set(FIXES) - seen)
    if missing:
        raise SystemExit(f"Missing stable_ids in dataset: {missing}")

    DATASET.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(f"touched={touched} dataset={DATASET}")


if __name__ == "__main__":
    main()
