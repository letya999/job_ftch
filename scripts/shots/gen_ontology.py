"""Generate derived ontology from positive and negative shots."""

import json
import os
import sys
from pathlib import Path

import structlog
from openai import OpenAI

logger = structlog.get_logger(__name__)

# Ensure stdout is utf-8
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore


def main() -> None:
    shots_path = Path("fixtures/shots/all_shots.jsonl")
    if not shots_path.exists():
        logger.error("shots_file_not_found", path=str(shots_path))
        sys.exit(1)

    pos_vacancies = []
    neg_vacancies = []
    pos_resume = []
    neg_resume = []

    with shots_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            shot = json.loads(line)
            kind = shot.get("kind")
            label = shot.get("label")
            text = shot.get("text", "")
            if kind == "vacancy":
                if label == "positive":
                    pos_vacancies.append(text)
                elif label == "negative":
                    neg_vacancies.append(text)
            elif kind == "resume":
                if label == "positive":
                    pos_resume.append(text)
                elif label == "negative":
                    neg_resume.append(text)

    prompt = [
        "You are an expert ontology extractor.",
        "Your task is to analyze a candidate's resume and a set of positive and negative job postings.",
        "Based on these, extract a derived ontology that sharply separates the positive from the negative jobs.",
        "JUDGE BY RESPONSIBILITIES and concrete technologies, not generic buzzwords.",
        "You must output ONLY a JSON object with this exact schema:",
        '{"profile_description":"<str>", "positive_keywords":[{"term":"<str>","weight":<int>}], "negative_keywords":[{"term":"<str>","weight":<int>}], "anti_patterns":["<str>"], "skills":["<str>"], "roles":["<str>"], "negative_roles":["<str>"]}',
        "Requirements:",
        "- `profile_description`: 1-2 sentences, RESPONSIBILITY-framed, describing the candidate target strictly from resume + positive vacancies (applied-LLM product engineering). MUST NOT invent categories absent from the shots. MUST NOT enumerate negative roles.",
        "- `positive_keywords`: technologies/skills/role-signals that characterise POSITIVE vacancies. Weight 1-5.",
        "- `negative_keywords`: signals that characterise NEGATIVE vacancies. Weight 1-5.",
        "- `anti_patterns`: short phrases that should down-rank an item (discriminative negative concepts).",
        "- `skills`: normalised canonical list of skills.",
        "- `roles`: canonical applied-LLM target roles derived from positive shots.",
        "- `negative_roles`: canonical roles from negative shots (DS, MLOps, CV, Research, DataEng, PM, recsys, LLM-training) - for anti_preferences.",
        "- Deduplicate items.",
        "- Add obvious synonyms (e.g., 'large language models' for 'LLM').",
        "",
        "## CANDIDATE BACKGROUND (positive resume - what the candidate IS / has done)",
    ]

    for r in pos_resume:
        prompt.append(f"---\n{r}")

    prompt.append(
        "\n## NOT THE CANDIDATE (negative resume - profiles the candidate is NOT / not interested in)"
    )
    for nr in neg_resume:
        prompt.append(f"---\n{nr}")

    prompt.append("\n## POSITIVE VACANCIES (jobs the candidate WANTS)")
    for pv in pos_vacancies:
        prompt.append(f"---\n{pv}")

    prompt.append("\n## NEGATIVE VACANCIES (jobs the candidate REJECTS)")
    for nv in neg_vacancies:
        prompt.append(f"---\n{nv}")

    prompt_text = "\n".join(prompt)

    api_key = os.environ.get("JOB_FTCH_OPENAI_API_KEY")
    if not api_key:
        logger.error("missing_openai_api_key")
        sys.exit(1)

    model_name = os.environ.get("JOB_FTCH_ONTOLOGY_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)

    logger.info("generating_ontology", model=model_name)
    response = client.chat.completions.create(
        model=model_name,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You output JSON only."},
            {"role": "user", "content": prompt_text},
        ],
        temperature=0.2,
    )

    result_text = response.choices[0].message.content.strip()  # type: ignore

    try:
        ontology = json.loads(result_text)
    except json.JSONDecodeError as exc:
        logger.error("ontology_json_decode_error", error=str(exc))
        sys.exit(1)

    ontology["positive_examples"] = pos_vacancies
    ontology["negative_examples"] = neg_vacancies

    out_path = Path("fixtures/shots/derived_ontology.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(ontology, f, ensure_ascii=False, indent=2)

    logger.info("ontology_saved", path=str(out_path))

    # Print summary
    pos_count = len(ontology.get("positive_keywords", []))
    neg_count = len(ontology.get("negative_keywords", []))
    anti_count = len(ontology.get("anti_patterns", []))
    skills_count = len(ontology.get("skills", []))
    roles_count = len(ontology.get("roles", []))
    pos_ex_count = len(ontology.get("positive_examples", []))
    neg_ex_count = len(ontology.get("negative_examples", []))

    top_pos = sorted(
        ontology.get("positive_keywords", []), key=lambda x: x.get("weight", 0), reverse=True
    )[:5]
    top_neg = sorted(
        ontology.get("negative_keywords", []), key=lambda x: x.get("weight", 0), reverse=True
    )[:5]

    print("=== DERIVED ONTOLOGY SUMMARY ===")
    print(f"Positive keywords: {pos_count}")
    print(f"Negative keywords: {neg_count}")
    print(f"Anti-patterns: {anti_count}")
    print(f"Skills: {skills_count}")
    print(f"Roles: {roles_count}")
    print(f"Positive examples: {pos_ex_count}")
    print(f"Negative examples: {neg_ex_count}")
    print("\nTop Positive Signals:")
    for item in top_pos:
        print(f"  - {item.get('term')} (weight: {item.get('weight')})")
    print("\nTop Negative Signals:")
    for item in top_neg:
        print(f"  - {item.get('term')} (weight: {item.get('weight')})")
    print("================================")


if __name__ == "__main__":
    main()
