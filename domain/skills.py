# domain/skills.py
"""Domain-level skill normalization (pure functions)."""

import re
from typing import Set, List
from difflib import get_close_matches

# ============ Константы ============

CANONICAL_SKILLS: Set[str] = {
    "python",
    "javascript",
    "typescript",
    "java",
    "go",
    "rust",
    "cpp",
    "csharp",
    "ruby",
    "php",
    "swift",
    "kotlin",
    "scala",
    "react",
    "vue",
    "angular",
    "node.js",
    "django",
    "flask",
    "fastapi",
    "spring",
    "machine learning",
    "artificial intelligence",
    "deep learning",
    "nlp",
    "llm",
    "generative ai",
    "rag",
    "tensorflow",
    "pytorch",
    "keras",
    "langchain",
    "openai",
    "bert",
    "transformer",
    "sql",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "elasticsearch",
    "cassandra",
    "docker",
    "kubernetes",
    "aws",
    "gcp",
    "azure",
    "terraform",
    "jenkins",
    "ci/cd",
    "git",
    "pandas",
    "numpy",
    "scikit-learn",
    "tableau",
    "power bi",
    "kafka",
    "airflow",
    "spark",
}

# Отдельный набор для коротких навыков (2-3 символа), требующих контекста
SHORT_SKILLS: dict[str, str] = {
    "r": "r",  # Язык R, но только если есть контекст
    "c": "c",  # Язык C (тоже с контекстом)
    "go": "go",  # Go - нормальный, оставляем
}

SKILL_SYNONYMS: dict[str, str] = {
    "python": "python",
    "py": "python",
    "python3": "python",
    "python 3": "python",
    "javascript": "javascript",
    "js": "javascript",
    "ecmascript": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "java": "java",
    "java 8": "java",
    "java 11": "java",
    "java 17": "java",
    "go": "go",
    "golang": "go",
    "golang ": "go",
    "rust": "rust",
    "rustlang": "rust",
    "c++": "cpp",
    "cpp": "cpp",
    "cplusplus": "cpp",
    "c#": "csharp",
    "csharp": "csharp",
    "dotnet": "csharp",
    "ruby": "ruby",
    "ruby on rails": "ruby",
    "rails": "ruby",
    "php": "php",
    "php7": "php",
    "php8": "php",
    "swift": "swift",
    "ios": "swift",
    "kotlin": "kotlin",
    "android": "kotlin",
    "scala": "scala",
    "spark": "scala",
    "react": "react",
    "reactjs": "react",
    "react.js": "react",
    "vue": "vue",
    "vuejs": "vue",
    "vue.js": "vue",
    "angular": "angular",
    "angularjs": "angular",
    "angular2": "angular",
    "node": "node.js",
    "nodejs": "node.js",
    "node.js": "node.js",
    "django": "django",
    "django rest": "django",
    "flask": "flask",
    "flask api": "flask",
    "fastapi": "fastapi",
    "fast api": "fastapi",
    "spring": "spring",
    "spring boot": "spring",
    "springboot": "spring",
    "machine learning": "machine learning",
    "ml": "machine learning",
    "artificial intelligence": "artificial intelligence",
    "ai": "artificial intelligence",
    "deep learning": "deep learning",
    "dl": "deep learning",
    "nlp": "nlp",
    "natural language processing": "nlp",
    "llm": "llm",
    "large language model": "llm",
    "large language models": "llm",
    "generative ai": "generative ai",
    "genai": "generative ai",
    "gen ai": "generative ai",
    "rag": "rag",
    "retrieval augmented generation": "rag",
    "tensorflow": "tensorflow",
    "tf": "tensorflow",
    "tensor flow": "tensorflow",
    "pytorch": "pytorch",
    "torch": "pytorch",
    "py torch": "pytorch",
    "keras": "keras",
    "kera": "keras",
    "langchain": "langchain",
    "lang chain": "langchain",
    "openai": "openai",
    "open ai": "openai",
    "gpt": "openai",
    "chatgpt": "openai",
    "bert": "bert",
    "transformer": "transformer",
    "sql": "sql",
    "structured query language": "sql",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "pg": "postgresql",
    "mysql": "mysql",
    "mariadb": "mysql",
    "mongodb": "mongodb",
    "mongo": "mongodb",
    "mongo db": "mongodb",
    "redis": "redis",
    "redis cache": "redis",
    "elasticsearch": "elasticsearch",
    "elastic search": "elasticsearch",
    "es": "elasticsearch",
    "cassandra": "cassandra",
    "cassandra db": "cassandra",
    "docker": "docker",
    "docker container": "docker",
    "container": "docker",
    "kubernetes": "kubernetes",
    "k8s": "kubernetes",
    "kube": "kubernetes",
    "aws": "aws",
    "amazon web services": "aws",
    "ec2": "aws",
    "s3": "aws",
    "gcp": "gcp",
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "azure": "azure",
    "microsoft azure": "azure",
    "terraform": "terraform",
    "tf": "terraform",
    "iac": "terraform",
    "jenkins": "jenkins",
    "ci/cd": "ci/cd",
    "cicd": "ci/cd",
    "ci cd": "ci/cd",
    "git": "git",
    "github": "git",
    "gitlab": "git",
    "version control": "git",
    "pandas": "pandas",
    "numpy": "numpy",
    "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "tableau": "tableau",
    "power bi": "power bi",
    "looker": "looker",
    "kafka": "kafka",
    "apache kafka": "kafka",
    "airflow": "airflow",
    "apache airflow": "airflow",
    "spark": "spark",
    "apache spark": "spark",
    "pyspark": "spark",
}

# ============ Основные функции нормализации ============


def normalize_skill(skill: str) -> str:
    """Normalize a single skill name."""
    skill_lower = skill.lower().strip()

    # Пропускаем односимвольные навыки (кроме 'c' и 'r' с контекстом)
    if len(skill_lower) == 1 and skill_lower not in ["c", "r"]:
        return ""

    if skill_lower in SKILL_SYNONYMS:
        return SKILL_SYNONYMS[skill_lower]

    if skill_lower.endswith("s") and skill_lower[:-1] in SKILL_SYNONYMS:
        return SKILL_SYNONYMS[skill_lower[:-1]]

    if skill_lower in CANONICAL_SKILLS:
        return skill_lower

    matches = get_close_matches(skill_lower, CANONICAL_SKILLS, n=1, cutoff=0.8)
    return matches[0] if matches else skill_lower


def normalize_skills(skills: List[str]) -> List[str]:
    """Normalize a list of skills."""
    normalized = set()
    for s in skills:
        norm = normalize_skill(s)
        if norm:  # Пустые строки отбрасываем
            normalized.add(norm)
    return sorted(normalized)


# ============ Извлечение навыков из текста ============

SKILL_SEARCH_PATTERNS: Set[str] = {
    # Языки (исключая односимвольные)
    "python",
    "javascript",
    "typescript",
    "java",
    "go",
    "rust",
    "cpp",
    "csharp",
    "ruby",
    "php",
    "swift",
    "kotlin",
    "scala",
    # Фреймворки
    "react",
    "vue",
    "angular",
    "node.js",
    "django",
    "flask",
    "fastapi",
    "spring",
    # AI/ML
    "machine learning",
    "deep learning",
    "nlp",
    "llm",
    "rag",
    "generative ai",
    "tensorflow",
    "pytorch",
    "keras",
    "langchain",
    "openai",
    "transformers",
    "bert",
    "transformer",
    "vector database",
    # Базы данных
    "sql",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "elasticsearch",
    # DevOps
    "docker",
    "kubernetes",
    "aws",
    "gcp",
    "azure",
    "terraform",
    "jenkins",
    "ci/cd",
    "git",
    # Data
    "pandas",
    "numpy",
    "scikit-learn",
    "tableau",
    "power bi",
    "spark",
    "airflow",
    "kafka",
}

# Контекстные паттерны для коротких навыков (требуют особой проверки)
CONTEXT_PATTERNS: dict[str, list[str]] = {
    "r": [
        "r language",
        "r programming",
        "r studio",
        "r syntax",
        "r package",
        " data analysis with r",
        " statistical ",
    ],
    "c": ["c language", "c programming", "c development", "c code", "ansi c"],
}


def extract_skills_from_text(text: str) -> List[str]:
    """
    Extract and normalize skills from text.
    Uses pattern matching and synonym resolution.
    """
    if not text:
        return []

    text_lower = text.lower()
    found = set()

    # Direct pattern matching for normal skills
    for pattern in SKILL_SEARCH_PATTERNS:
        # Try word boundary match first
        if re.search(r"\b" + re.escape(pattern) + r"\b", text_lower):
            found.add(pattern)
        # Then simple substring for multi-word
        elif pattern in text_lower:
            found.add(pattern)

    # Special handling for short skills (R language, C language)
    # Check for 'R' only if it has context
    for short_skill, contexts in CONTEXT_PATTERNS.items():
        for ctx in contexts:
            if ctx in text_lower:
                found.add(short_skill)
                break

    # Handle special cases from your original code
    if "ml" in text_lower and "machine learning" not in found:
        found.add("machine learning")
    if "ai" in text_lower and "artificial intelligence" not in found:
        found.add("artificial intelligence")
    if "k8s" in text_lower:
        found.add("kubernetes")
    if "tf" in text_lower and "tensorflow" not in found:
        if "tensor" in text_lower or "flow" in text_lower:
            found.add("tensorflow")

    # Normalize each found skill
    normalized = set()
    for skill in found:
        norm = normalize_skill(skill)
        if norm and len(norm) > 1:
            normalized.add(norm)

    return sorted(normalized)[:15]
