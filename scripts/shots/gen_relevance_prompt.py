"""Generate a relevance system prompt from positive and negative shots."""

import asyncio
import json
import os
import sys
from pathlib import Path

import structlog
from openai import OpenAI

try:
    import tiktoken

    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

# Try importing the application registry to persist to store if available.
try:
    from job_ftch.application.registry import create_store_with_fallback
    from job_ftch.config import get_settings

    HAS_STORE = True
except ImportError:
    HAS_STORE = False

logger = structlog.get_logger(__name__)

# Ensure stdout is utf-8
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore


def main() -> None:
    shots_path = Path("fixtures/shots/all_shots.jsonl")
    if not shots_path.exists():
        logger.error("shots_file_not_found", path=str(shots_path))
        sys.exit(1)

    shots = []
    with shots_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            shots.append(json.loads(line))

    pos_vacancies = []
    neg_vacancies = []
    resume_context = []

    for shot in shots:
        kind = shot.get("kind")
        label = shot.get("label")
        text = shot.get("text", "")
        if kind == "vacancy":
            if label == "positive":
                pos_vacancies.append(text)
            elif label == "negative":
                neg_vacancies.append(text)
        elif kind == "resume":
            resume_context.append(text)

    meta_prompt = [
        "You are an expert recruitment system prompt engineer.",
        "Your task is to analyze a candidate's profile and preferences (based on their resume and examples of jobs they accepted and rejected) and write a system prompt.",
        "The system prompt you write will be given to another LLM to filter new job postings.",
        "",
        "REQUIREMENTS FOR THE GENERATED SYSTEM PROMPT:",
        "1. Must be plain instructional text.",
        "2. Derive the accept/reject boundary STRICTLY from the provided positive/negative vacancy examples + resume; describe it in terms of RESPONSIBILITIES (building/integrating LLM/AI into products vs building/training/researching models, classic DS, infra, analytics, management).",
        "3. A job TITLE token alone (ML/DL/NLP/Research/Engineer) must NEVER by itself cause rejection - judge by the stated responsibilities.",
        "4. Do not introduce role categories that are not represented in the examples.",
        "5. NO hardcoded company or title blocklists.",
        "6. Be concise and compact.",
        "",
        "Here is the context about the candidate:",
    ]

    if resume_context:
        meta_prompt.append("## CANDIDATE RESUME/PROFILE")
        for r in resume_context:
            meta_prompt.append(r)
        meta_prompt.append("")

    meta_prompt.append("## JOBS THE CANDIDATE LIKES (POSITIVE EXAMPLES)")
    for i, pv in enumerate(pos_vacancies, 1):
        meta_prompt.append(f"--- Example {i} ---\n{pv}\n")

    meta_prompt.append("## JOBS THE CANDIDATE REJECTS (NEGATIVE EXAMPLES)")
    for i, nv in enumerate(neg_vacancies, 1):
        meta_prompt.append(f"--- Example {i} ---\n{nv}\n")

    meta_prompt_text = "\n".join(meta_prompt)

    api_key = os.environ.get("JOB_FTCH_OPENAI_API_KEY")
    if not api_key:
        logger.error("missing_openai_api_key")
        sys.exit(1)

    model_name = os.environ.get("JOB_FTCH_PROMPT_GEN_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)

    logger.info("generating_prompt", model=model_name)
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": meta_prompt_text}],
        temperature=0.2,
    )

    generated_prompt = response.choices[0].message.content.strip()  # type: ignore

    if HAS_TIKTOKEN:
        try:
            encoding = tiktoken.encoding_for_model(model_name)
            token_count = len(encoding.encode(generated_prompt))
        except Exception:
            token_count = len(generated_prompt) // 4
    else:
        token_count = len(generated_prompt) // 4

    print("=== GENERATED SYSTEM PROMPT ===")
    print(generated_prompt)
    print("===============================")
    print(f"Token estimate: {token_count}")

    out_path = Path("fixtures/shots/generated_relevance_prompt.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(generated_prompt, encoding="utf-8")
    logger.info("prompt_saved", path=str(out_path))

    if HAS_STORE:

        async def save_to_kv():  # type: ignore
            try:
                settings = get_settings()
                store = await create_store_with_fallback(settings)
                await store.set_run_state("relevance:generated_prompt:default", generated_prompt)  # type: ignore
                logger.info("prompt_persisted_to_kv")
            except Exception as exc:
                logger.warning("prompt_kv_persist_failed", error=str(exc))

        asyncio.run(save_to_kv())  # type: ignore


if __name__ == "__main__":
    main()
