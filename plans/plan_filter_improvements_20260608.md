# Filtering Pipeline Improvements

Date: 2026-06-08
Branch: phase-17-21

## Overview

Four targeted improvements that keep the pipeline simple while significantly improving
precision and recall:

1. Word-boundary keyword matching — fix substring false positives ("ai" in "training")
2. `post_type` + `ai_relevance` in ExtractionNode schema — move semantic classification
   into the existing LLM call (zero extra LLM calls, solves Russian-language recall)
3. `ClassifierProvider` protocol + `SourceClassifierNode` — pluggable pre-extraction
   classifier with KeywordClassifier (zero deps) / LLMClassifier / SetFitClassifier
   (optional `[classifiers]` extras) implementations
4. `CompanyCanonicalizer` node (RM-136) — legal suffix stripping + alias YAML via
   rapidfuzz (already a dep), populates the existing `Job.company_canonical` field

## Architecture Rules (must not be violated)

- `domain/` imports only pydantic + stdlib — NO exceptions
- `application/` imports only `domain/` + stdlib + pydantic
- `nodes/`, `sinks/` import only `domain/` + `application/`
- `infrastructure/` can import anything above + external clients
- SanitizeNode must always be first in any pipeline
- No hardcoded secrets, credentials, or company names in code — all in YAML/env
- English only: code, comments, commits
- No AI attribution in any output

## Pipeline Order After Changes

```
SanitizeNode
→ HeuristicTriageNode      (structural: text length, obvious patterns)
→ SourceClassifierNode     (NEW: pre-extraction semantic gate)
→ DedupNode
→ ExtractionNode           (EXTENDED: +post_type, +ai_relevance in same LLM call)
→ CompanyCanonicalizer     (NEW: RM-136, populates job.company_canonical)
→ AIRoleRelevanceNode      (SIMPLIFIED: reads job.ai_relevance when available)
→ QualityScoringNode
→ JobValidationNode
```

---

## 1. domain/models.py — Add PostType enum and new Job fields

Add after the `WorkMode` enum:

```python
class PostType(StrEnum):
    JOB_POSTING = "job_posting"
    CANDIDATE_SEEKING = "candidate_seeking"
    ANNOUNCEMENT = "announcement"
    SPAM = "spam"
    UNKNOWN = "unknown"
```

Add to the `Job` model (after `relevance_score` field, all with defaults so zero
breaking changes):

```python
post_type: PostType = PostType.UNKNOWN
ai_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
```

Note: `Job.company_canonical` already exists in the model — no change needed there.

---

## 2. domain/filter_profile.py — Add RU keywords and classifier signal patterns

Modify `FilterProfile` model: add two new optional list fields with defaults:

```python
candidate_signal_patterns: list[str] = Field(default_factory=lambda: [
    "#candidate",
    "#резюме",
    "#ищуработу",
    "#opentowork",
    "about me:",
    "looking for opportunities",
    "open to work",
    "hi, i'm",
    "привет, меня зовут",
    "ищу работу",
    "в поиске работы",
])

spam_signal_patterns: list[str] = Field(default_factory=lambda: [
    r"\d{3,}\.\d{2}\s*odds",
    "скачать музыку",
    "скачать песню",
    r"нажми.{0,20}скачать",
    "casino",
    "betting",
    "букмекер",
])
```

Modify `FilterProfile.default()` to update `positive_relevance_keywords` to include
Russian-language AI terms alongside existing English ones. The full default list should be:

```python
positive_relevance_keywords=[
    "ai",
    "llm",
    "genai",
    "mlops",
    "ml",
    "machine learning",
    "agent",
    "rag",
    "prompt",
    "infra",
    "platform",
    "data scientist",
    "ai pm",
    "ai product",
    "deep learning",
    "neural",
    "nlp",
    "computer vision",
    "data science",
    # Russian
    "машинное обучение",
    "нейронные сети",
    "большие языковые модели",
    "искусственный интеллект",
    "нлп",
    "глубокое обучение",
    "языковая модель",
]
```

Note: Remove "ml " (trailing space variant) since word boundary fix (change 3 below)
makes it unnecessary.

---

## 3. nodes/triage.py — Word-boundary keyword matching

Replace the `_has_any` helper with a version that uses word boundaries for short
keywords (to prevent "ai" matching "training"). The key fix:

```python
import re
from functools import lru_cache

@lru_cache(maxsize=256)
def _compile_keyword(kw: str) -> re.Pattern[str]:
    # Use word boundary for keywords <=5 chars with no spaces (e.g. "ai", "ml", "rag")
    # Use substring match for multi-word phrases (e.g. "machine learning")
    stripped = kw.strip()
    if len(stripped) <= 5 and " " not in stripped:
        return re.compile(rf"\b{re.escape(stripped)}\b", re.IGNORECASE)
    return re.compile(re.escape(stripped), re.IGNORECASE)

def _has_any(text: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(_compile_keyword(p).search(text) for p in patterns)
```

This is the ONLY logic change to this function. The rest of `triage.py` is unchanged.

---

## 4. application/contracts.py — Add ClassifierProvider protocol

Add after the existing `LLMProvider` protocol (read the file first to find the right
location — add after LLMProvider, before or after Store, keeping the file structure):

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ClassificationResult:
    label: str        # PostType string value
    confidence: float  # 0.0-1.0
    model_id: str

@runtime_checkable
class ClassifierProvider(Protocol):
    async def classify(self, text: str) -> ClassificationResult:
        """Classify a single text item. Returns PostType label + confidence."""

    async def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        """Classify multiple texts. Default impl can call classify() in a loop."""

    @property
    def model_id(self) -> str:
        """Identifier for logging and metrics."""
```

Important: `ClassificationResult` should be defined as a `dataclass` or in a way that
does NOT require pydantic (to keep application/ layer clean). Plain `dataclass` or
`NamedTuple` is fine.

---

## 5. infrastructure/classifiers/__init__.py (new, empty)

---

## 6. infrastructure/classifiers/keyword_classifier.py (new)

`KeywordClassifierProvider` — pure rule-based, zero external deps:

```python
class KeywordClassifierProvider:
    model_id = "keyword_v1"

    def __init__(self, *, profile: FilterProfile | None = None) -> None:
        self._profile = profile or FilterProfile.default()

    async def classify(self, text: str) -> ClassificationResult:
        lowered = text.casefold()
        # Check spam patterns (regex-based)
        for pattern in self._profile.spam_signal_patterns:
            if re.search(pattern, lowered):
                return ClassificationResult("spam", 0.95, self.model_id)
        # Check candidate patterns (substring)
        if any(p.casefold() in lowered for p in self._profile.candidate_signal_patterns):
            return ClassificationResult("candidate_seeking", 0.90, self.model_id)
        return ClassificationResult("unknown", 0.5, self.model_id)

    async def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        results = []
        for text in texts:
            results.append(await self.classify(text))
        return results
```

Note: Returns "unknown" (not "job_posting") when no negative signal is found. The
SourceClassifierNode only DROPS on high-confidence negative labels. Unknown = pass through.

---

## 7. infrastructure/classifiers/llm_classifier.py (new)

`LLMClassifierProvider` — wraps existing LLMProvider with a classification schema:

```python
from pydantic import BaseModel, Field

class _PostTypeSchema(BaseModel):
    post_type: PostType
    ai_relevance: float = Field(ge=0.0, le=1.0)
    reasoning: str  # brief explanation, not stored but helps LLM quality

class LLMClassifierProvider:
    model_id = "llm_classifier_v1"

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def classify(self, text: str) -> ClassificationResult:
        prompt = (
            "Classify this text. Is it a job posting (job_posting), "
            "a person seeking work (candidate_seeking), an announcement/event (announcement), "
            "or spam/unrelated (spam)? "
            "Also rate ai_relevance: 0.0=not AI/ML role, 1.0=clearly AI/ML role.\n\n"
            f"Text:\n{text[:600]}"
        )
        try:
            result = await self._llm.extract(prompt, _PostTypeSchema)
            label = result.post_type.value
            confidence = result.ai_relevance if label == "job_posting" else 0.9
            return ClassificationResult(label, confidence, self.model_id)
        except Exception:
            return ClassificationResult("unknown", 0.0, self.model_id)

    async def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        results = []
        for text in texts:
            results.append(await self.classify(text))
        return results
```

---

## 8. infrastructure/classifiers/setfit_classifier.py (new)

`SetFitClassifierProvider` — fine-tunable SetFit classifier using
`sentence-transformers/multilingual-e5-small` as the base model. This is an OPTIONAL
heavy dep, guarded by try/except:

```python
# Optional dep guard at module top
try:
    from setfit import SetFitModel  # type: ignore[import-untyped]
    _SETFIT_AVAILABLE = True
except ImportError:
    _SETFIT_AVAILABLE = False

LABELS = ["job_posting", "candidate_seeking", "announcement", "spam", "unknown"]
DEFAULT_MODEL = "sentence-transformers/multilingual-e5-small"
TRAINING_DATA_PATH = Path("fixtures/classifier_training")

class SetFitClassifierProvider:
    model_id: str

    def __init__(self, model_path: str | Path | None = None) -> None:
        if not _SETFIT_AVAILABLE:
            raise ImportError(
                "SetFit is not installed. Run: pip install 'job_ftch[classifiers]'"
            )
        path = str(model_path or DEFAULT_MODEL)
        self._model = SetFitModel.from_pretrained(path)
        self.model_id = f"setfit:{path}"

    async def classify(self, text: str) -> ClassificationResult:
        loop = asyncio.get_running_loop()
        preds = await loop.run_in_executor(None, self._model.predict, [text])
        label = str(preds[0])
        proba = self._model.predict_proba([text])[0]
        confidence = float(max(proba))
        return ClassificationResult(label, confidence, self.model_id)

    async def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        if not texts:
            return []
        loop = asyncio.get_running_loop()
        preds = await loop.run_in_executor(None, self._model.predict, texts)
        probas = await loop.run_in_executor(None, self._model.predict_proba, texts)
        return [
            ClassificationResult(str(label), float(max(proba)), self.model_id)
            for label, proba in zip(preds, probas)
        ]
```

Add a `train()` classmethod that reads JSONL from `fixtures/classifier_training/` and
fine-tunes using SetFit. Format of training data:
`{"text": "...", "label": "job_posting"}` one per line.

Also create `fixtures/classifier_training/.gitkeep` (empty, so the dir is tracked).

---

## 9. nodes/source_classifier.py (new)

`SourceClassifierNode` — pre-extraction semantic gate:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from application.drops import RawItemDropped
from domain import RawItem, TriageRejectionReason

if TYPE_CHECKING:
    from application.contracts import ClassifierProvider

_REJECTED_LABELS = frozenset({"candidate_seeking", "spam"})

class SourceClassifierNode:
    def __init__(
        self,
        classifier: ClassifierProvider,
        *,
        confidence_threshold: float = 0.80,
    ) -> None:
        self._classifier = classifier
        self._threshold = confidence_threshold

    async def process(self, item: RawItem) -> RawItem | None:
        result = await self._classifier.classify(item.text)
        if result.label in _REJECTED_LABELS and result.confidence >= self._threshold:
            raise RawItemDropped(
                reason=TriageRejectionReason.IRRELEVANT_CONTENT,
                details=(
                    f"Classifier rejected item: label={result.label!r} "
                    f"confidence={result.confidence:.2f} model={result.model_id!r}"
                ),
                item=item,
            )
        return item
```

Threshold = 0.80: conservative. We prefer letting ambiguous items through to LLM
extraction over false-dropping a real job. Only high-confidence negatives are dropped.

---

## 10. nodes/extraction.py — Extend ExtractedJobFields schema

Extend `ExtractedJobFields` Pydantic model to include classification fields:

```python
from domain import PostType  # new import

class ExtractedJobFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company: str | None = None
    description: str | None = None
    canonical_url: AnyHttpUrl | None = None
    location: str | None = None
    work_mode: WorkMode | None = None
    compensation: CompensationRange | None = None
    # New fields — LLM classifies in the same call as extraction
    post_type: PostType = PostType.UNKNOWN
    ai_relevance: float = Field(default=0.0, ge=0.0, le=1.0,
        description="0.0 = not an AI/ML role. 1.0 = clearly AI/ML role.")
```

In `ExtractionNode.process()`, copy these fields to the Job object. Find the `return Job(...)`
block and add:

```python
return Job(
    ...existing fields...,
    post_type=extracted.post_type,
    ai_relevance=extracted.ai_relevance,
)
```

The `_extract_fields` method already returns `ExtractedJobFields` and handles failures
with a fallback to `ExtractedJobFields()` (which defaults to UNKNOWN and 0.0 — correct).

---

## 11. nodes/relevance.py — Simplify AIRoleRelevanceNode

When `job.ai_relevance > 0.0` (meaning ExtractionNode set it via LLM), use it directly
instead of re-doing keyword scoring. Fall back to keyword scoring only when it's 0.0
(e.g. LLM extraction failed and we got the default).

```python
async def process(self, item: Job) -> Job | None:
    haystack = " ".join(
        part for part in (item.title or "", item.company or "", item.description) if part
    ).casefold()

    # Negative check (always applies)
    if any(keyword in haystack for keyword in self._profile.negative_relevance_keywords):
        raise RawItemDropped(
            reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
            details="Job matches an explicit non-target role pattern.",
            item=item,
            stage=self.__class__.__name__,
        )

    # If LLM already computed ai_relevance during extraction, use it directly
    if item.ai_relevance > 0.0:
        if item.ai_relevance <= self._profile.relevance_threshold:
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details=f"LLM-estimated ai_relevance={item.ai_relevance:.2f} is below threshold.",
                item=item,
                stage=self.__class__.__name__,
            )
        return item  # already has relevance_score set by LLM
    
    # Also drop candidate/spam post types detected during extraction
    from domain import PostType  # avoid circular at module level, use local import
    if item.post_type in (PostType.CANDIDATE_SEEKING, PostType.SPAM):
        raise RawItemDropped(
            reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
            details=f"Extracted post_type={item.post_type.value!r} is not a job posting.",
            item=item,
            stage=self.__class__.__name__,
        )

    # Fallback: keyword scoring (when ai_relevance was not set by LLM)
    if not self._profile.positive_relevance_keywords:
        return item
    positive_hits = sum(
        1 for keyword in self._profile.positive_relevance_keywords if keyword in haystack
    )
    relevance_score = min(1.0, positive_hits / 3.0)
    if relevance_score <= self._profile.relevance_threshold:
        raise RawItemDropped(
            reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
            details="Job does not match the target AI-jobs niche (score too low).",
            item=item,
            stage=self.__class__.__name__,
        )
    return item.model_copy(update={"relevance_score": relevance_score})
```

---

## 12. domain/company.py (new)

Pure stdlib + constants. No pydantic needed here (this is a utility, not a model).

```python
"""Company name normalization utilities."""
from __future__ import annotations
import re

LEGAL_SUFFIXES: tuple[str, ...] = (
    "ООО", "ПАО", "ЗАО", "АО", "АКБ", "ОАО", "НКО",
    "Ltd", "Inc", "LLC", "GmbH", "SRL", "S.A.", "Corp", "Co.",
)

_SUFFIX_PATTERN = re.compile(
    r"(?i)\s*\b(" + "|".join(re.escape(s) for s in LEGAL_SUFFIXES) + r")\b\.?\s*",
)
_WHITESPACE = re.compile(r"\s+")

def normalize_company_name(raw: str) -> str:
    """Strip legal suffixes, normalize whitespace, casefold."""
    cleaned = _SUFFIX_PATTERN.sub(" ", raw)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned.casefold()
```

---

## 13. nodes/company.py (new) — CompanyCanonicalizer

```python
"""Company name canonicalization node (RM-136)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from rapidfuzz import fuzz

from domain import Job
from domain.company import normalize_company_name

if TYPE_CHECKING:
    pass

_DEFAULT_THRESHOLD = 85  # token_set_ratio threshold for fuzzy alias matching

class CompanyCanonicalizer:
    def __init__(
        self,
        aliases_path: Path | None = None,
        *,
        fuzzy_threshold: int = _DEFAULT_THRESHOLD,
    ) -> None:
        self._threshold = fuzzy_threshold
        # _aliases: {normalized_alias -> canonical_name}
        self._aliases: dict[str, str] = {}
        if aliases_path and aliases_path.exists():
            self._load_aliases(aliases_path)

    def _load_aliases(self, path: Path) -> None:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for canonical, alias_list in data.items():
            # Register canonical itself
            self._aliases[normalize_company_name(canonical)] = canonical
            for alias in (alias_list or []):
                self._aliases[normalize_company_name(alias)] = canonical

    def _resolve(self, raw: str) -> str | None:
        normalized = normalize_company_name(raw)
        if normalized in self._aliases:
            return self._aliases[normalized]
        # Fuzzy fallback
        for alias_key, canonical in self._aliases.items():
            score = fuzz.token_set_ratio(normalized, alias_key)
            if score >= self._threshold:
                return canonical
        return None

    async def process(self, item: Job) -> Job | None:
        if not item.company:
            return item
        canonical = self._resolve(item.company)
        if canonical and canonical != item.company_canonical:
            return item.model_copy(update={"company_canonical": canonical})
        return item
```

---

## 14. config/company_aliases.yaml (new)

```yaml
# Canonical company name → list of known aliases
# Format: "Canonical Name": ["alias1", "alias2", ...]
Yandex:
  - "Яндекс"
  - "ООО Яндекс"
  - "Yandex N.V."
  - "Yandex LLC"
  - "Яндекс.Технологии"
Sberbank:
  - "Сбер"
  - "Сбербанк"
  - "ПАО Сбербанк"
  - "СберТех"
  - "Sber"
  - "SberTech"
VK:
  - "ВК"
  - "VK Group"
  - "ООО ВКонтакте"
  - "Mail.ru Group"
  - "VK Company"
Tinkoff:
  - "Тинькофф"
  - "Тинькофф Банк"
  - "T-Bank"
  - "АО Тинькофф Банк"
Ozon:
  - "Озон"
  - "OZON"
  - "ООО Интернет Решения"
Kaspi:
  - "Каспи"
  - "Kaspi.kz"
  - "АО Kaspi Bank"
Avito:
  - "Авито"
  - "ООО КЕХ еКоммерц"
MTS:
  - "МТС"
  - "ПАО МТС"
Alfa-Bank:
  - "Альфа-Банк"
  - "АО Альфа-Банк"
  - "Alfa Bank"
```

---

## 15. profiles/ai_jobs_ru_kz.yaml (new)

Create a ready-to-use FilterProfile config file:

```yaml
# FilterProfile for AI/ML jobs in Russia and Kazakhstan
# Load with: FilterProfile.model_validate(yaml.safe_load(path.read_text()))
positive_relevance_keywords:
  - "ai"
  - "llm"
  - "genai"
  - "mlops"
  - "ml"
  - "machine learning"
  - "agent"
  - "rag"
  - "prompt engineering"
  - "data scientist"
  - "data science"
  - "deep learning"
  - "neural"
  - "nlp"
  - "computer vision"
  - "машинное обучение"
  - "нейронные сети"
  - "большие языковые модели"
  - "искусственный интеллект"
  - "нлп"
  - "глубокое обучение"
  - "языковая модель"
negative_relevance_keywords:
  - "sales"
  - "account executive"
  - "recruiter"
  - "office manager"
  - "marketing"
exclude_keywords:
  - "subscribe"
  - "follow us"
  - "webinar"
  - "meetup"
  - "conference"
  - "newsletter"
  - "digest"
  - "podcast"
relevance_threshold: 0.0
min_text_tokens: 10
min_text_chars: 50
```

---

## 16. fixtures/classifier_training/.gitkeep (new, empty)

This directory will hold training examples for SetFitClassifierProvider in JSONL format
once enough labeled data is collected.

---

## 17. pyproject.toml — Add [classifiers] extras group

Find the `[project.optional-dependencies]` section and add:

```toml
classifiers = [
    "setfit>=0.6.0",
    "sentence-transformers>=2.6.0",
]
```

Also update the `[all]` group to include `classifiers` if it exists.

---

## 18. tests/test_source_classifier.py (new)

Tests for SourceClassifierNode + KeywordClassifierProvider:

1. `test_keyword_classifier_detects_candidate_post` — text with `#candidate` →
   label=`candidate_seeking`, confidence >= 0.80
2. `test_keyword_classifier_detects_spam_odds` — text with "483.00 odds" →
   label=`spam`, confidence >= 0.80
3. `test_keyword_classifier_passes_unknown` — normal ML job text →
   label=`unknown`, any confidence
4. `test_source_classifier_node_drops_candidate` — SourceClassifierNode with
   KeywordClassifierProvider drops a `#candidate` post (raises RawItemDropped or returns None)
5. `test_source_classifier_node_passes_unknown` — SourceClassifierNode passes through
   a normal job post with label=`unknown`
6. `test_source_classifier_node_respects_threshold` — item classified as candidate with
   confidence=0.70 (below 0.80 threshold) should PASS through, not be dropped
7. `test_llm_classifier_uses_provider` — mock LLMProvider that returns a
   `_PostTypeSchema` with `post_type=PostType.JOB_POSTING`, `ai_relevance=0.9` →
   verify ClassificationResult has correct label

---

## 19. tests/test_company_canonicalizer.py (new)

1. `test_normalizes_legal_suffix_ooo` — "ООО Яндекс" → normalized = "яндекс"
2. `test_normalizes_legal_suffix_pao` — "ПАО Сбербанк" → normalized = "сбербанк"
3. `test_alias_exact_match` — CompanyCanonicalizer with aliases, "Яндекс" → "Yandex"
4. `test_alias_fuzzy_match` — "Сбер" → "Sberbank" (via fuzzy)
5. `test_no_alias_file_is_noop` — CompanyCanonicalizer() with no aliases_path →
   `process(job)` returns same job unchanged (company_canonical stays None)
6. `test_already_canonical_unchanged` — job with company_canonical already set correctly
   → no change
7. `test_normalizer_domain_function_purity` — `normalize_company_name` called with
   "  ООО  Тинькофф Банк  " → "тинькофф банк" (whitespace + suffix stripped)

---

## Quality Gates

Run in this order after implementation:

```
uv run ruff format .
uv run ruff check .
uv run mypy .
uv run pytest tests/ -v --ignore=tests/e2e
```

Also verify hexagonal boundary is clean:
```
rg "from infrastructure" domain/ application/ nodes/ sinks/
```
Must return empty.

---

## Constraints Summary

1. `domain/models.py`: `PostType` enum uses stdlib `StrEnum` — no new deps
2. `domain/company.py`: only stdlib (re, typing) — no pydantic, no rapidfuzz
3. `nodes/company.py`: may use rapidfuzz (already a dep) and yaml (already a dep)
4. `infrastructure/classifiers/setfit_classifier.py`: must guard `import setfit`
   with try/except and raise ImportError with install instructions
5. `SourceClassifierNode` threshold default = 0.80 — conservative, prefer false
   negatives over false positives in pre-extraction gate
6. `CompanyCanonicalizer` with no aliases_path must be a no-op (graceful degradation)
7. All new Job fields (`post_type`, `ai_relevance`) must have defaults — zero
   breaking changes to existing serialized Job objects
8. No co-authorship lines in commits, no AI attribution anywhere
