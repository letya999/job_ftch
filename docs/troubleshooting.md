# Troubleshooting

## Telegram auth errors
- Re-check `JOB_FTCH_TELEGRAM_API_ID` and `JOB_FTCH_TELEGRAM_API_HASH`.
- Delete a bad local session file only if you intend to re-authenticate from scratch.
- Use a public handle in `JOB_FTCH_TELEGRAM_ENTITY` first before trying invite-style targets.

## Telegram flood waits
- Increase `JOB_FTCH_TELEGRAM_HISTORY_WAIT_TIME_SECONDS`.
- Lower `JOB_FTCH_TELEGRAM_MESSAGE_LIMIT` or comment limits.
- Keep `JOB_FTCH_TELEGRAM_REQUEST_RETRIES` bounded; do not set infinite retries.

## Career-site allowlist failures
- `career_site_url` must be HTTPS.
- The host must appear in `JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS`.
- Check redirects; the final host also needs to be allowed.

## Extraction quality problems
- Start with `JOB_FTCH_LLM_BACKEND=heuristic` for offline debugging.
- Run `scripts/evaluate_extraction.py` against the gold fixture after changing extraction logic.
- Review `artifacts/debug/review.jsonl` for borderline jobs instead of only the main output.

## Missing output file after a failed run
- Check for staged sink files next to the target output path.
- Re-run the same command; the JSON sink keeps staged payloads for recovery after a failed finalize step.
- Inspect `artifacts/debug/rejected.jsonl` and `artifacts/debug/quarantine.jsonl` before widening filters.

## Oversized or malformed inputs
- `SanitizeNode` now rejects pathological text length through `JOB_FTCH_PIPELINE_MAX_TEXT_LENGTH`.
- Rejected and quarantined records keep snapshots for operator feedback.
