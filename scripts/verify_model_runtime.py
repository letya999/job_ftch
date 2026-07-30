"""Verify production model caches using the same runtime identity and loader."""

from __future__ import annotations


def main() -> None:
    from job_ftch.infrastructure.embeddings.bgem3 import BgeMThreeProvider

    provider = BgeMThreeProvider(model_name="BAAI/bge-m3", use_fp16=False)
    vector = provider.encode("runtime verification")["dense"]
    if len(vector) != provider.dim:
        raise RuntimeError("BGE-M3 runtime verification produced an empty vector")


if __name__ == "__main__":
    main()
