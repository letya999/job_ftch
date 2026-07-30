from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

from job_ftch.application.shot_sync import remove_user_shots_async, sync_profile_to_shot_store
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import get_settings
from job_ftch.infrastructure.document_parser import parse_document
from scripts.mvp_data_repair import rebuild_ontology

DEFAULT_PDF_DIR = Path(r"C:\Users\User\Downloads\job_ftch_pdf_shots_40\job_ftch_pdf_shots")
DEFAULT_SHOTS_FILE = Path("fixtures/shots/all_shots.jsonl")
DEFAULT_TENANT_ID = "ai_jobs"
DEFAULT_USER_ID = "480637186"
DEFAULT_PROFILE_ID = "user_480637186"
DEFAULT_CONFIGS_DIR = Path("job_ftch/adapters/telegram_bot/config/tenants")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild user shots from a PDF directory and repopulate Qdrant.",
    )
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--shots-file", type=Path, default=DEFAULT_SHOTS_FILE)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--profile-id", default=DEFAULT_PROFILE_ID)
    parser.add_argument("--configs-dir", type=Path, default=DEFAULT_CONFIGS_DIR)
    parser.add_argument(
        "--skip-ontology",
        action="store_true",
        help="Only refresh profile examples and Qdrant; leave Postgres ontology untouched.",
    )
    return parser.parse_args()


def _kind_and_label(path: Path) -> tuple[str, str]:
    parent = path.parent.name
    if parent == "positive_resumes":
        return ("resume", "positive")
    if parent == "negative_resumes":
        return ("resume", "negative")
    if parent == "positive_vacancies":
        return ("vacancy", "positive")
    if parent == "negative_vacancies":
        return ("vacancy", "negative")
    raise ValueError(f"Unexpected shot directory: {path.parent}")


def _role_from_name(path: Path) -> str:
    stem = path.stem
    parts = stem.split("_")
    if len(parts) <= 3:
        return stem
    return " ".join(parts[3:]).replace("-", " ")


def _load_shots(pdf_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(pdf_dir.rglob("*.pdf")):
        kind, label = _kind_and_label(path)
        text = parse_document(path.read_bytes(), path.name).strip()
        if not text:
            raise ValueError(f"Empty extracted text: {path}")
        shot_id = (
            ("res" if kind == "resume" else "vac")
            + "_"
            + ("pos" if label == "positive" else "neg")
            + "_"
            + f"{len(rows):02d}"
        )
        rows.append(
            {
                "id": shot_id,
                "kind": kind,
                "label": label,
                "role": _role_from_name(path),
                "text": text,
            }
        )
    if len(rows) != 40:
        raise ValueError(f"Expected 40 PDF shots, found {len(rows)}")
    return rows


def _write_shots_file(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _replace_profile_examples(record: Any, rows: list[dict[str, Any]]) -> Any:
    if not record.profile.search_profiles:
        raise ValueError("Candidate profile has no search profiles")
    profile = record.profile.search_profiles[0]
    positive_resume = tuple(
        row["text"] for row in rows if row["kind"] == "resume" and row["label"] == "positive"
    )
    negative_resume = tuple(
        row["text"] for row in rows if row["kind"] == "resume" and row["label"] == "negative"
    )
    positive_job = tuple(
        row["text"] for row in rows if row["kind"] == "vacancy" and row["label"] == "positive"
    )
    negative_job = tuple(
        row["text"] for row in rows if row["kind"] == "vacancy" and row["label"] == "negative"
    )
    updated_profile = profile.model_copy(
        update={
            "positive_example_texts": positive_resume,
            "negative_example_texts": negative_resume,
            "positive_job_example_texts": positive_job,
            "negative_job_example_texts": negative_job,
        }
    )
    return record.model_copy(
        update={
            "profile": record.profile.model_copy(
                update={"search_profiles": (updated_profile,) + record.profile.search_profiles[1:]}
            )
        }
    )


def _clear_profile_shot_collections(settings: Any) -> list[str]:
    client = QdrantClient(
        url=str(settings.qdrant_url),
        api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
    )
    names = [
        item.name
        for item in client.get_collections().collections
        if item.name.startswith("profile_shots")
    ]
    for name in names:
        client.delete_collection(name)
    return names


async def main() -> int:
    args = parse_args()
    rows = _load_shots(args.pdf_dir)
    _write_shots_file(args.shots_file, rows)

    settings = get_settings()
    tenants = load_tenants(args.configs_dir)
    runner = TenantRunner.from_tenants(tenants, base_settings=settings)
    try:
        record = await runner.get_candidate_profile(args.tenant_id, args.user_id, args.profile_id)
        if record is None:
            raise RuntimeError("Candidate profile not found")
        updated = _replace_profile_examples(record, rows)
        await runner.save_and_activate_candidate_profile(args.tenant_id, updated)

        cleared = _clear_profile_shot_collections(settings)
        await remove_user_shots_async(tenant_id=args.tenant_id, user_id=args.user_id)
        pos_count, neg_count = await sync_profile_to_shot_store(
            profile=updated,
            tenant_id=args.tenant_id,
            user_id=args.user_id,
        )
        seed_cmd = [
            sys.executable,
            "scripts/dev/seed_profile_shots_qdrant.py",
            "--shots-file",
            str(args.shots_file),
            "--tenant-id",
            args.tenant_id,
            "--user-id",
            args.user_id,
            "--profile-id",
            args.profile_id,
        ]
        seed_run = subprocess.run(seed_cmd, check=True, capture_output=True, text=True)
        seed_payload = json.loads(seed_run.stdout)
        ontology_payload: dict[str, Any] | None = None
        if not args.skip_ontology:
            ontology_payload = await rebuild_ontology(apply=True)
        print(
            json.dumps(
                {
                    "tenant_id": args.tenant_id,
                    "user_id": args.user_id,
                    "profile_id": args.profile_id,
                    "shots_written": len(rows),
                    "qdrant_written": seed_payload["qdrant_written"],
                    "collection": seed_payload["collection"],
                    "collections_cleared": cleared,
                    "in_memory_counts": {"positive": pos_count, "negative": neg_count},
                    "ontology_rebuild": ontology_payload,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        await runner.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
