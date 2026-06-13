# Plan: Phase 4 — Ontology & Normalization Layer

Date: 2026-06-12
Suite baseline: 286 passed, 8 skipped

## Goal

Bootstrap a static-first ontology layer for role and skill normalization (ru/en).
No ESCO API calls, no heavy runtime downloads. Bundled static alias tables as JSON data files
in `infrastructure/ontology/data/`. Logic in `infrastructure/ontology/`. Node wired into pipeline.

This closes the Critical gap: `SkillTag.skill_id` never populated, `role_family` from 3 hardcoded tokens.

## Architectural constraints (HARD RULES — no exceptions)

- `domain/` stays pydantic + stdlib only. No ontology imports.
- Alias data goes in `job_ftch/infrastructure/ontology/data/` as JSON files.
- `OntologyNormalizer` goes in `job_ftch/infrastructure/ontology/normalizer.py`.
- A new `SkillNormalizationNode` goes in `job_ftch/nodes/job_normalization.py`.
- `TitleCompanyNormalizationNode` is updated to use `OntologyNormalizer` for role_family/seniority.
- No external deps required (pure Python json + stdlib).
- `kk` and `uz` ready by design (empty alias lists, not hard-coded language checks).

---

## Files to create

### 1. `job_ftch/infrastructure/ontology/__init__.py`
Empty init to make it a package.

### 2. `job_ftch/infrastructure/ontology/data/role_aliases.json`
JSON file with role family definitions. Each family has a list of keyword aliases (casefold matches).
Content (minimum viable, extend later):

```json
{
  "engineering": {
    "aliases_en": ["engineer", "developer", "dev ", "architect", "programmer", "coder", "backend", "frontend", "fullstack", "full stack", "full-stack", "mobile", "ios dev", "android dev"],
    "aliases_ru": ["разработчик", "инженер", "программист", "бэкенд", "фронтенд", "фуллстек", "мобильный разработчик"]
  },
  "data": {
    "aliases_en": ["data scientist", "data engineer", "machine learning", "ml engineer", "ai engineer", "data analyst", "analytics engineer", "bi developer", "bi engineer", "nlp", "computer vision"],
    "aliases_ru": ["аналитик данных", "data scientist", "data engineer", "машинное обучение", "ml", "аналитик bi", "ml-инженер", "ai-инженер"]
  },
  "devops": {
    "aliases_en": ["devops", "sre", "site reliability", "platform engineer", "infrastructure", "infra engineer", "cloud engineer", "devsecops"],
    "aliases_ru": ["devops", "sre", "платформенный инженер", "инфраструктурный инженер", "облачный инженер"]
  },
  "product": {
    "aliases_en": ["product manager", "product owner", "pm ", "head of product", "director of product"],
    "aliases_ru": ["менеджер продукта", "product manager", "product owner", "руководитель продукта"]
  },
  "design": {
    "aliases_en": ["designer", "ux ", "ui ", "ux/ui", "ui/ux", "product designer", "visual designer", "interaction designer", "design lead"],
    "aliases_ru": ["дизайнер", "ux-дизайнер", "ui-дизайнер", "продуктовый дизайнер"]
  },
  "management": {
    "aliases_en": ["engineering manager", "tech lead", "team lead", "director of engineering", "head of engineering", "vp of engineering", "cto"],
    "aliases_ru": ["руководитель разработки", "тимлид", "технический директор", "директор по разработке"]
  },
  "qa": {
    "aliases_en": ["qa engineer", "quality assurance", "tester", "sdet", "test engineer", "qa lead", "automation engineer"],
    "aliases_ru": ["qa-инженер", "тестировщик", "специалист по тестированию", "инженер по тестированию"]
  },
  "security": {
    "aliases_en": ["security engineer", "infosec", "penetration tester", "pentester", "appsec", "cybersecurity", "soc analyst"],
    "aliases_ru": ["инженер по безопасности", "специалист по информационной безопасности", "пентестер", "кибербезопасность"]
  },
  "research": {
    "aliases_en": ["researcher", "research engineer", "research scientist", "applied scientist"],
    "aliases_ru": ["исследователь", "научный сотрудник", "исследовательский инженер"]
  },
  "analytics": {
    "aliases_en": ["analyst", "business analyst", "data analyst", "financial analyst", "marketing analyst", "growth analyst"],
    "aliases_ru": ["аналитик", "бизнес-аналитик", "аналитик данных", "финансовый аналитик"]
  }
}
```

### 3. `job_ftch/infrastructure/ontology/data/seniority_aliases.json`
Maps seniority levels to keyword aliases:

```json
{
  "intern": {
    "aliases_en": ["intern", "internship", "trainee", "student"],
    "aliases_ru": ["стажёр", "стажер", "практикант", "студент"]
  },
  "junior": {
    "aliases_en": ["junior", "jr.", "jr ", "entry level", "entry-level", "graduate"],
    "aliases_ru": ["джуниор", "junior", "начинающий", "начинающий специалист"]
  },
  "middle": {
    "aliases_en": ["middle", "mid ", "mid-level", "midlevel", "medior"],
    "aliases_ru": ["мидл", "middle", "специалист", "разработчик"]
  },
  "senior": {
    "aliases_en": ["senior", "sr.", "sr ", "experienced"],
    "aliases_ru": ["сеньор", "senior", "опытный", "ведущий специалист"]
  },
  "lead": {
    "aliases_en": ["lead ", "tech lead", "team lead", "staff"],
    "aliases_ru": ["лид", "лидер", "ведущий разработчик", "тимлид"]
  },
  "principal": {
    "aliases_en": ["principal", "distinguished", "fellow"],
    "aliases_ru": ["главный", "principal"]
  },
  "head": {
    "aliases_en": ["head of", "director", "vp ", "vice president", "cto", "cpo", "ciso"],
    "aliases_ru": ["руководитель", "директор", "глава", "начальник", "вице-президент"]
  }
}
```

### 4. `job_ftch/infrastructure/ontology/data/skill_aliases.json`
Maps canonical skill_id slug → display name + aliases (ru/en).
This is a minimal but representative set for common tech jobs. Extend over time.

```json
{
  "python": {
    "canonical_name": "Python",
    "aliases_en": ["python", "python3", "python 3", "py "],
    "aliases_ru": ["python", "питон", "пайтон"]
  },
  "javascript": {
    "canonical_name": "JavaScript",
    "aliases_en": ["javascript", "js", "ecmascript", "es6", "es2015"],
    "aliases_ru": ["javascript", "джаваскрипт", "js"]
  },
  "typescript": {
    "canonical_name": "TypeScript",
    "aliases_en": ["typescript", "ts ", " ts,"],
    "aliases_ru": ["typescript", "тайпскрипт"]
  },
  "go": {
    "canonical_name": "Go",
    "aliases_en": ["golang", " go ", "go lang", "go/"],
    "aliases_ru": ["golang", "голанг", " go "]
  },
  "rust": {
    "canonical_name": "Rust",
    "aliases_en": ["rust", "rustlang"],
    "aliases_ru": ["rust", "раст"]
  },
  "java": {
    "canonical_name": "Java",
    "aliases_en": ["java ", "java,", "java8", "java11", "java17", "spring boot"],
    "aliases_ru": ["java", "джава"]
  },
  "kotlin": {
    "canonical_name": "Kotlin",
    "aliases_en": ["kotlin"],
    "aliases_ru": ["kotlin", "котлин"]
  },
  "scala": {
    "canonical_name": "Scala",
    "aliases_en": ["scala"],
    "aliases_ru": ["scala"]
  },
  "csharp": {
    "canonical_name": "C#",
    "aliases_en": ["c#", "csharp", ".net", "dotnet", "asp.net"],
    "aliases_ru": ["c#", "csharp", ".net", "дотнет"]
  },
  "cpp": {
    "canonical_name": "C++",
    "aliases_en": ["c++", "cpp", "c/c++"],
    "aliases_ru": ["c++", "плюсплюс"]
  },
  "swift": {
    "canonical_name": "Swift",
    "aliases_en": ["swift", "swiftui", "ios"],
    "aliases_ru": ["swift", "ios"]
  },
  "react": {
    "canonical_name": "React",
    "aliases_en": ["react", "reactjs", "react.js", "react native", "next.js", "nextjs"],
    "aliases_ru": ["react", "реакт"]
  },
  "vue": {
    "canonical_name": "Vue.js",
    "aliases_en": ["vue", "vuejs", "vue.js", "nuxt"],
    "aliases_ru": ["vue", "вью"]
  },
  "angular": {
    "canonical_name": "Angular",
    "aliases_en": ["angular", "angularjs"],
    "aliases_ru": ["angular", "ангуляр"]
  },
  "postgresql": {
    "canonical_name": "PostgreSQL",
    "aliases_en": ["postgresql", "postgres", "pg "],
    "aliases_ru": ["postgresql", "postgres", "постгрес"]
  },
  "mysql": {
    "canonical_name": "MySQL",
    "aliases_en": ["mysql", "mariadb"],
    "aliases_ru": ["mysql", "марiadб"]
  },
  "mongodb": {
    "canonical_name": "MongoDB",
    "aliases_en": ["mongodb", "mongo"],
    "aliases_ru": ["mongodb", "монго"]
  },
  "redis": {
    "canonical_name": "Redis",
    "aliases_en": ["redis"],
    "aliases_ru": ["redis", "редис"]
  },
  "elasticsearch": {
    "canonical_name": "Elasticsearch",
    "aliases_en": ["elasticsearch", "elastic", "opensearch"],
    "aliases_ru": ["elasticsearch", "эластик"]
  },
  "kafka": {
    "canonical_name": "Apache Kafka",
    "aliases_en": ["kafka", "apache kafka"],
    "aliases_ru": ["kafka", "кафка"]
  },
  "rabbitmq": {
    "canonical_name": "RabbitMQ",
    "aliases_en": ["rabbitmq", "rabbit mq", "amqp"],
    "aliases_ru": ["rabbitmq"]
  },
  "kubernetes": {
    "canonical_name": "Kubernetes",
    "aliases_en": ["kubernetes", "k8s", "k8"],
    "aliases_ru": ["kubernetes", "кубернетес", "k8s"]
  },
  "docker": {
    "canonical_name": "Docker",
    "aliases_en": ["docker", "dockerfile", "docker-compose", "docker compose"],
    "aliases_ru": ["docker", "докер"]
  },
  "aws": {
    "canonical_name": "AWS",
    "aliases_en": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "aliases_ru": ["aws", "amazon web services"]
  },
  "gcp": {
    "canonical_name": "GCP",
    "aliases_en": ["gcp", "google cloud", "google cloud platform"],
    "aliases_ru": ["gcp", "google cloud"]
  },
  "azure": {
    "canonical_name": "Azure",
    "aliases_en": ["azure", "microsoft azure"],
    "aliases_ru": ["azure", "microsoft azure", "ажур"]
  },
  "terraform": {
    "canonical_name": "Terraform",
    "aliases_en": ["terraform", "tf "],
    "aliases_ru": ["terraform", "терраформ"]
  },
  "graphql": {
    "canonical_name": "GraphQL",
    "aliases_en": ["graphql", "graph ql"],
    "aliases_ru": ["graphql"]
  },
  "grpc": {
    "canonical_name": "gRPC",
    "aliases_en": ["grpc", "protocol buffers", "protobuf"],
    "aliases_ru": ["grpc", "протобаф"]
  },
  "git": {
    "canonical_name": "Git",
    "aliases_en": ["git", "github", "gitlab", "bitbucket"],
    "aliases_ru": ["git", "гит"]
  },
  "linux": {
    "canonical_name": "Linux",
    "aliases_en": ["linux", "ubuntu", "debian", "centos", "rhel", "bash", "shell scripting"],
    "aliases_ru": ["linux", "линукс", "bash", "шелл"]
  }
}
```

### 5. `job_ftch/infrastructure/ontology/normalizer.py`
Core lookup class. Pure Python, no external deps. Loaded once at import time (module-level singleton).

**Class: `OntologyNormalizer`**

Constructor: `__init__(self, data_dir: Path | None = None)`
- Loads three JSON files from `data_dir` (defaults to `Path(__file__).parent / "data"`)
- Builds inverted lookup dicts for fast matching: `{alias_substr → (family, canonical)}` 

Methods:
- `infer_role_family(title: str, language: str = "unknown") -> str | None`
  - Lowercase the title
  - Iterate role families in priority order
  - Return first family whose any alias substring is found in title
  - Returns None if no match
- `infer_seniority(title: str) -> str | None`
  - Same approach for seniority levels
  - Returns canonical seniority key string or None
- `normalize_skill(skill_name: str) -> tuple[str, str | None]`
  - Returns `(canonical_name, skill_id)` 
  - Looks up skill_name.casefold() against alias substrings
  - Returns `(original_name, None)` if no match (never loses data)
- `normalize_skills(skills: tuple[SkillTag, ...]) -> tuple[SkillTag, ...]`
  - Maps each SkillTag through normalize_skill
  - Returns new tuple with skill_id populated where matched

Singleton pattern: module-level `_DEFAULT_NORMALIZER: OntologyNormalizer | None = None` and `get_default_normalizer() -> OntologyNormalizer` factory.

### 6. Update `job_ftch/nodes/job_normalization.py`

**Update `TitleCompanyNormalizationNode`:**
- Import `OntologyNormalizer` from `job_ftch.infrastructure.ontology.normalizer`
- Accept optional `normalizer: OntologyNormalizer | None = None` in `__init__`
- If normalizer is None, use `get_default_normalizer()` from ontology module
- Replace the 3-bucket role_family logic with `normalizer.infer_role_family(title)`
- Add seniority inference: if `item.seniority is Seniority.UNKNOWN`, call `normalizer.infer_seniority(title)` and map the string result to `Seniority` enum value
- Add `normalization_steps` entries for these enrichments

**Add new class `SkillNormalizationNode`** at the end of `job_normalization.py`:
- Constructor: `__init__(self, normalizer: OntologyNormalizer | None = None)`
- Uses `get_default_normalizer()` if no normalizer passed
- `async process(self, item: JobRecord) -> JobRecord | None`
  - Calls `normalizer.normalize_skills(item.skills_explicit)` and `normalizer.normalize_skills(item.skills_inferred)`
  - If any skill_id changed: returns `item.model_copy(update={...skills...})` with provenance note
  - Otherwise returns item unchanged

### 7. Update `job_ftch/nodes/__init__.py`
Add `SkillNormalizationNode` to imports and `__all__`.

### 8. Update `job_ftch/application/builder.py`

In `build_nodes()`, insert `SkillNormalizationNode()` after `TitleCompanyNormalizationNode()`:
```python
TitleCompanyNormalizationNode(),
SkillNormalizationNode(),          # new
LocationWorkModeNormalizationNode(),
```

Also update the import at the top to include `SkillNormalizationNode`.

---

## Files to create for tests

### 9. `tests/test_phase4_ontology.py`
Tests that verify:

1. `test_role_family_inferred_from_english_title()` — "Senior Backend Engineer" → role_family="engineering"
2. `test_role_family_inferred_from_russian_title()` — "Старший разработчик Python" → role_family="engineering"
3. `test_seniority_inferred_from_title()` — "Senior Product Manager" → seniority="senior"
4. `test_seniority_inferred_ru()` — "Джуниор разработчик" → seniority="junior"
5. `test_skill_normalization_known_skill()` — SkillTag(canonical_name="python3") → skill_id="python"
6. `test_skill_normalization_unknown_skill_preserved()` — SkillTag(canonical_name="obscure_framework") → skill_id=None, canonical_name unchanged
7. `test_skill_normalization_node_enriches_record()` — pipeline: JobRecord with skills_explicit=[SkillTag("javascript")] → after SkillNormalizationNode, skill_id="javascript"
8. `test_title_normalization_uses_ontology()` — full TitleCompanyNormalizationNode.process() with a JobDraft that has title="ML Engineer" → role_family="data"
9. `test_ontology_normalizer_no_match_returns_none()` — title="Coordinator of Special Projects" → role_family=None (no false positives)

---

## Seniority enum mapping

In `job_ftch/domain/models.py`, check the `Seniority` enum values. The normalizer returns string keys like "junior", "senior", "lead" etc. The `SkillNormalizationNode`/`TitleCompanyNormalizationNode` should map these to the existing `Seniority` enum.

Look up `class Seniority(StrEnum)` in `domain/models.py` first and map accordingly. If `Seniority` has values `JUNIOR`, `SENIOR` etc, map "junior" → `Seniority.JUNIOR`. If there's no close match for "lead" or "principal", map to the nearest available or skip.

---

## Execution order

1. Create `job_ftch/infrastructure/ontology/__init__.py` (empty)
2. Create `job_ftch/infrastructure/ontology/data/` directory and all 3 JSON files
3. Create `job_ftch/infrastructure/ontology/normalizer.py`
4. Update `job_ftch/nodes/job_normalization.py` (update existing class + add new class)
5. Update `job_ftch/nodes/__init__.py`
6. Update `job_ftch/application/builder.py`
7. Create `tests/test_phase4_ontology.py`
8. Run `python -m pytest tests/test_phase4_ontology.py tests/test_job_quality.py -q -o addopts="" --tb=short`
9. Run full suite: `python -m pytest tests -q -o addopts="" --tb=short`

## Success criteria

- `pytest tests -q` >= 286 passed, 0 failures
- `python -c "from job_ftch.infrastructure.ontology.normalizer import OntologyNormalizer; n=OntologyNormalizer(); print(n.infer_role_family('Senior Backend Engineer'))"` prints "engineering"
- `python -c "from job_ftch.infrastructure.ontology.normalizer import OntologyNormalizer; n=OntologyNormalizer(); print(n.normalize_skill('python3'))"` prints `('Python', 'python')`
- `rg "SkillNormalizationNode" job_ftch/nodes/__init__.py` returns a match
- `rg "SkillNormalizationNode" job_ftch/application/builder.py` returns a match
- `TitleCompanyNormalizationNode` no longer has 3-bucket hardcoded role_family logic
- `job_ftch/infrastructure/ontology/data/` exists with 3 JSON files
