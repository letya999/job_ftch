"""Enrich golden labels.jsonl with full expected pipeline output.

For each item determines:
  - Confirmed post_type (re-classifies REVIEW items)
  - exit_stage: which pipeline stage drops it (null = passes all stages)
  - drop_reason: why it was dropped
  - Structured expected fields for job_posting items:
      title, company, seniority, work_mode, language, compensation,
      skills, ai_relevance

Usage:
    python scripts/enrich_golden_dataset.py
    python scripts/enrich_golden_dataset.py --golden fixtures/dataset/labels.jsonl
    python scripts/enrich_golden_dataset.py --stats
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── channel priors ─────────────────────────────────────────────────────────

# Primary job-board channels: >95% of posts ARE job postings.
# An unknown item here defaults to job_posting unless it has strong anti-signal.
_PRIMARY_JOB_CHANNELS = frozenset(
    {
        "datasciencejobs",
        "remote_ai_jobs",
        "ml_jobs_kz",
        "llm_jobs",
        "vakansii_chatgpt",
        "python_jobs_ru",
        "avito_career",
        "data_secrets_career",
        "mari_vakansii",
    }
)

# Patterns that are clearly NOT job postings even inside job channels.
_ANTI_JOB_PATTERNS = (
    "дайджест",
    "подборка",
    "запись",
    "трансляц",
    "вебинар",
    "конференц",
    "митап",
    "воркшоп",
    "саммит",
    "прямой эфир",
    "нетворкинг",
    "магистратур",
    "соревновани",  # соревнования/соревнований — ML competitions, hackathons
    "хакатон",  # hackathon in Russian
    "digest",
    "webinar",
    "meetup",
    "workshop",
    "recording",
    "podcast",
    "hackathon",
    "open to work",
    "#резюме",
    "#candidate",
)

# ─── extraction heuristics ───────────────────────────────────────────────────

_SENIORITY_MAP = {
    "intern": ("intern", "стажер", "стажёр", "интерн"),
    "junior": ("junior", "джуниор", "джун", "начинающий"),
    "middle": ("middle", "мидл", "middl"),
    "senior": ("senior", "сеньор", "старший"),
    "lead": ("lead", "лид", "тимлид", "team lead"),
    "staff": ("staff",),
    "principal": ("principal",),
    "head": ("head of", "руководитель", "начальник"),
    "director": ("director", "директор"),
}

_WORK_MODE_MAP = {
    "remote": ("remote", "удален", "удалён", "удалённо", "удаленка", "дистанцион"),
    "hybrid": ("hybrid", "гибрид"),
    "onsite": ("onsite", "on-site", "офис", "в офисе", "в офис", "office", "очно"),
}

_AI_SKILLS = frozenset(
    {
        "python",
        "ml",
        "machine learning",
        "deep learning",
        "llm",
        "nlp",
        "pytorch",
        "tensorflow",
        "keras",
        "scikit",
        "huggingface",
        "transformers",
        "bert",
        "gpt",
        "langchain",
        "rag",
        "embeddings",
        "vector",
        "faiss",
        "mlops",
        "airflow",
        "kubeflow",
        "mlflow",
        "triton",
        "data science",
        "data scientist",
        "компьютерное зрение",
        "computer vision",
        "reinforcement learning",
        "rl",
        "generative",
        "diffusion",
        "cv",
        "spark",
        "sql",
        "hadoop",
        "data engineer",
        "аналитик",
        "analyst",
    }
)

_COMP_RE = re.compile(
    r"(?:"
    r"(\d[\d\s]{2,8}\d)\s*(?:тыс\.?|к)?\s*(?:₽|руб\.?|rub)"
    r"|(\d[\d\s]{2,8}\d)\s*(?:тыс\.?|к)?\s*(?:\$|usd|USD)"
    r"|(\d[\d\s]{2,8}\d)\s*(?:тыс\.?|к)?\s*(?:€|eur|EUR)"
    r"|(\d[\d\s]{2,8}\d)\s*(?:тыс\.?|к)?\s*(?:kzt|KZT|тенге)"
    r"|(\d[\d\s]{2,8}\d)\s*(?:k|K)\s*(?:\$|usd|USD)"
    r")",
    re.IGNORECASE,
)

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _detect_language(text: str) -> str:
    cyr = len(_CYRILLIC_RE.findall(text))
    lat = len(_LATIN_RE.findall(text))
    total = cyr + lat
    if total == 0:
        return "unknown"
    ratio = cyr / total
    if ratio >= 0.55:
        return "ru"
    if ratio <= 0.15:
        return "en"
    return "ru"  # mixed Ru-En posts are primarily Russian


def _extract_seniority(text: str) -> str:
    lowered = text.casefold()
    for level, tokens in _SENIORITY_MAP.items():
        if any(t in lowered for t in tokens):
            return level
    return "unknown"


def _extract_work_mode(text: str) -> str:
    lowered = text.casefold()
    for mode, tokens in _WORK_MODE_MAP.items():
        if any(t in lowered for t in tokens):
            return mode
    return "unknown"


def _extract_title(raw: dict[str, Any]) -> str | None:
    return raw.get("title")
    text = raw.get("text", "")
    # Use metadata title if available (career sites)
    for key in ("title", "job_title", "role"):
        val = raw.get("metadata", {}).get(key, "")
        if val and len(val) < 200:
            return val.strip()
    # First non-empty line is usually the title in TG posts
    for line in text.splitlines():
        line = line.strip().lstrip("📌📍🔥💼#🎯 \t")
        if len(line) >= 5 and len(line) <= 150:
            return line
    return None


def _extract_company(raw: dict[str, Any]) -> str | None:
    return raw.get("company")
    text = raw.get("text", "")
    for key in ("company", "company_name", "employer", "organization", "service"):
        val = raw.get("metadata", {}).get(key, "")
        if val:
            return val.strip()
    # "Компания: Avito" pattern
    m = re.search(r"компания[:\s]+([^\n,]{2,60})", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"company[:\s]+([^\n,]{2,60})", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _extract_skills(text: str) -> list[str]:
    lowered = text.casefold()
    found = []
    for skill in sorted(_AI_SKILLS):
        if skill in lowered:
            found.append(skill)
    return found[:10]


def _extract_compensation(text: str) -> dict | None:  # type: ignore
    m = _COMP_RE.search(text.replace(" ", " ").replace(" ", " "))
    if not m:
        return None
    raw_amount = next((g for g in m.groups() if g), "")
    digits = re.sub(r"\s", "", raw_amount)
    try:
        amount = int(digits)
    except ValueError:
        return None
    full = m.group(0)
    currency = "RUB"
    if "$" in full or "usd" in full.lower():
        currency = "USD"
    elif "€" in full or "eur" in full.lower():
        currency = "EUR"
    elif "kzt" in full.lower() or "тенге" in full.lower():
        currency = "KZT"
    if "тыс" in full.lower() or re.search(r"\bк\b", full, re.IGNORECASE):
        amount *= 1000
    if "k" in full.lower() and currency in ("USD", "EUR"):
        amount *= 1000
    return {"currency": currency, "min_amount": amount, "period": "month"}


def _ai_relevance(text: str) -> float:
    lowered = text.casefold()
    hits = sum(1 for s in _AI_SKILLS if s in lowered)
    return min(1.0, round(hits * 0.15, 2))


# ─── pipeline stage mapping ──────────────────────────────────────────────────


def _expected_pipeline(raw: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    """Return expected pipeline output for this item."""
    post_type = labels.get("post_type", "unknown")
    source_kind = raw.get("source_kind", "")
    text = raw.get("text", "")

    # Career sites ONLY emit job postings — heuristic mis-fires here are false positives.
    # Override non-job labels from career site items to job_posting.
    if source_kind == "career_site" and post_type != "job_posting":
        post_type = "job_posting"

    # Items dropped at hard_filter (post_type classified as non-job)
    if post_type in ("announcement", "spam", "candidate_seeking"):
        return {
            "exit_stage": "hard_filter",
            "drop_reason": "IRRELEVANT_CONTENT",
            "post_type": post_type,
        }

    # TG group items: unknown post_type → low semantic relevance → dropped at semantic_prefilter
    if source_kind == "telegram_group" and post_type == "unknown":
        return {
            "exit_stage": "semantic_prefilter",
            "drop_reason": "SEMANTIC_MISMATCH",
            "post_type": "unknown",
        }

    # Job postings — extract structured fields first
    if post_type == "job_posting":
        title = _extract_title(raw)
        company = _extract_company(raw)
        seniority = _extract_seniority(text)
        work_mode = _extract_work_mode(text)
        language = _detect_language(text)
        skills = _extract_skills(text)
        compensation = _extract_compensation(text)
        ai_rel = _ai_relevance(text)

        return {
            "exit_stage": None,
            "drop_reason": None,
            "post_type": "job_posting",
            "routing_decision": "ACCEPT",
            "title": title,
            "company": company,
            "seniority": seniority,
            "work_mode": work_mode,
            "language": language,
            "ai_relevance": ai_rel,
            "skills": skills,
            "compensation": compensation,
        }

    # Remaining unknowns (edge cases) — probably exit at semantic_prefilter
    return {
        "exit_stage": "semantic_prefilter",
        "drop_reason": "SEMANTIC_MISMATCH",
        "post_type": "unknown",
    }


# ─── REVIEW reclassification ─────────────────────────────────────────────────


def _reclassify_review(raw: dict[str, Any], labels: dict[str, Any]) -> str:
    """Return confirmed post_type for a REVIEW item."""
    import re as _re

    text = raw.get("text", "")
    lowered = text.casefold()
    source_name = raw.get("source_name", "")

    # Strong announcement signals override channel prior
    if any(p in lowered for p in _ANTI_JOB_PATTERNS):
        return "announcement"

    # Primary job channels: treat unknown as job_posting
    if source_name in _PRIMARY_JOB_CHANNELS:
        return "job_posting"

    # Mixed content channels: require at least one PRECISE job signal.
    # Use word-boundary checks so "engineering" doesn't match "engineer",
    # "research paper" doesn't match "researcher", etc.
    # Only strong, role-specific signals. Weak general words (опыт, навыки,
    # требования) are omitted because they appear equally in news articles.
    _JOB_WORD_SIGNALS = (
        r"\bинженер\b",
        r"\bразработчик\b",
        r"\bаналитик\b",
        r"\bспециалист\b",
        r"\bменеджер\b",
        r"\bархитектор\b",
        r"\bпозиция\b",
        r"\bengineer\b",  # word boundary: won't match "engineering"
        r"\bdeveloper\b",
        r"\bscientist\b",
        r"\banalyst\b",
        r"\bresearcher\b",
        r"\barchitect\b",
        r"\bзарплат",  # prefix: зарплата/зарплатой/зарплате
        r"\busd\b",
        r"\bkzt\b",
    )
    if any(_re.search(sig, lowered) for sig in _JOB_WORD_SIGNALS):
        return "job_posting"

    return "unknown"


# ─── main ────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--golden", default="fixtures/dataset/labels.jsonl")
    p.add_argument("--out", default=None, help="Output path (default: overwrite golden)")
    p.add_argument("--stats", action="store_true", help="Show enrichment stats and exit")
    return p.parse_args()


def _print_stats(path: Path) -> None:
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    exit_stages: Counter[str] = Counter()
    post_types: Counter[str] = Counter()
    routing: Counter[str] = Counter()
    for r in records:
        exp = r.get("expected", {})
        exit_stages[str(exp.get("exit_stage", "passes_all"))] += 1
        post_types[r["labels"].get("post_type", "?")] += 1
        routing[r["labels"].get("routing_decision", "?")] += 1

    print(f"\n{path}  ({len(records)} items)")
    print("\nExit stages:")
    for k, v in exit_stages.most_common():
        print(f"  {k:<30} {v:>4}")
    print("\nPost types:")
    for k, v in post_types.most_common():
        print(f"  {k:<22} {v:>4}")
    print("\nRouting:")
    for k, v in routing.most_common():
        print(f"  {k:<15} {v:>4}")


def main() -> int:
    args = parse_args()
    golden_path = Path(args.golden)

    if not golden_path.exists():
        print(f"ERROR: {golden_path} not found", file=sys.stderr)
        return 1

    if args.stats:
        _print_stats(golden_path)
        return 0

    records = [
        json.loads(line)
        for line in golden_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"Loaded {len(records)} records from {golden_path}")

    reclassified = 0
    enriched = 0
    post_type_counts: Counter[str] = Counter()

    out_records = []
    for rec in records:
        raw = rec.get("raw_item", {})
        labels = rec.get("labels", {})

        # Re-classify all items from non-trusted sources.
        # "Trusted" = primary job channels (>95% job posts) and career sites
        # (100% job posts).  For everything else we always re-derive the label
        # so enrichment is idempotent even if a prior run wrote a bad label back.
        is_career_site = raw.get("source_kind", "") == "career_site"
        is_primary_job_ch = raw.get("source_name", "") in _PRIMARY_JOB_CHANNELS
        if not is_career_site and not is_primary_job_ch:
            new_post_type = _reclassify_review(raw, labels)
            if new_post_type != labels.get("post_type", "unknown"):
                reclassified += 1
            labels = {**labels, "post_type": new_post_type}
            if new_post_type == "job_posting":
                labels["routing_decision"] = "ACCEPT"
            elif new_post_type == "announcement":
                labels["routing_decision"] = "REJECT"
            else:
                labels["routing_decision"] = "REVIEW"
        elif labels.get("routing_decision") == "REVIEW":
            # Primary job channels with explicit REVIEW label still get reclassified.
            new_post_type = _reclassify_review(raw, labels)
            if new_post_type != labels.get("post_type", "unknown"):
                reclassified += 1
            labels = {**labels, "post_type": new_post_type}
            if new_post_type == "job_posting":
                labels["routing_decision"] = "ACCEPT"
            elif new_post_type == "announcement":
                labels["routing_decision"] = "REJECT"

        # Build expected pipeline output
        expected = _expected_pipeline(raw, labels)
        enriched += 1
        post_type_counts[labels.get("post_type", "?")] += 1

        out_records.append(
            {
                **rec,
                "labels": labels,
                "expected": expected,
                "annotator": "auto_heuristic_v2",
            }
        )

    out_path = Path(args.out) if args.out else golden_path
    with out_path.open("w", encoding="utf-8") as fh:
        for r in out_records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nDone. Enriched {enriched} records → {out_path}")
    print(f"  Re-classified items: {reclassified}")
    print("\nFinal post_type distribution:")
    for k, v in post_type_counts.most_common():
        pct = v * 100 // len(out_records)
        print(f"  {k:<22} {v:>4}  ({pct}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
