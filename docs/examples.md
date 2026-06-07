# Examples

## Sample outputs
- Main job output: `fixtures/examples/job_output.json`
- Rejected-item output: `fixtures/examples/rejected_output.jsonl`

## Fixture-backed run

```bash
uv run python app.py \
  --source-path fixtures/e2e/multisource_positive.jsonl \
  --output-path artifacts/debug/jobs.json \
  --review-output-path artifacts/debug/review.jsonl \
  --rejected-output-path artifacts/debug/rejected.jsonl \
  --max-items 20
```

## Extraction evaluation

```bash
uv run python scripts/evaluate_extraction.py --fixture fixtures/extraction/gold_samples.jsonl --llm-backend heuristic
```

## Dry-run posting flow

```bash
uv run python app.py --source-path fixtures/e2e/multisource_positive.jsonl --posting-backend telegram_posting --dry-run
```
