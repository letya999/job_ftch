"""Typed ontology graph primitives for shot-derived profile evidence.

The new ontology layer stored flat positive/negative bags of roles, skills,
and keywords.  These models preserve the missing context: where the term came
from, how strong it is, and whether it is a target boundary or only
supporting/background evidence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OntologyNodeKind = Literal[
    "profile",
    "shot",
    "role",
    "skill",
    "keyword",
    "anti_pattern",
    "seniority",
    "claim",
]
OntologyPredicate = Literal[
    "asserts_role",
    "asserts_skill",
    "asserts_keyword",
    "asserts_anti_pattern",
    "asserts_seniority",
    "supports_role",
    "requires_skill",
    "excludes_role",
    "excludes_skill",
    "aliases",
]
OntologyPolarity = Literal["positive", "negative", "neutral", "contextual"]
OntologySemanticRole = Literal[
    "target_role",
    "anti_role",
    "current_role",
    "past_role",
    "target_skill",
    "supporting_skill",
    "background_skill",
    "anti_skill",
    "positive_keyword",
    "negative_keyword",
    "anti_pattern",
    "seniority",
    "context",
]
OntologyScope = Literal[
    "target",
    "supporting",
    "background",
    "anti",
    "contextual",
    "current",
    "past",
    "desired",
    "unknown",
]
OntologySourceSection = Literal[
    "title",
    "desired_role",
    "current_role",
    "past_role",
    "responsibilities",
    "requirements",
    "nice_to_have",
    "skills",
    "project",
    "summary",
    "anti_reason",
    "unknown",
]


class OntologyNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    kind: OntologyNodeKind
    canonical: str
    display: str | None = None
    lang: str = "en"
    attrs: dict[str, object] = Field(default_factory=dict)


class OntologyEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str
    subject_node_id: str
    predicate: OntologyPredicate
    object_node_id: str
    polarity: OntologyPolarity
    weight: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    arity_group_id: str | None = None
    attrs: dict[str, object] = Field(default_factory=dict)


class OntologyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    edge_id: str | None = None
    source_shot_id: str
    source_type: str
    source_section: OntologySourceSection = "unknown"
    text_span: str = ""
    extraction_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    model: str | None = None
    prompt_hash: str | None = None


class ExtractedOntologyClaim(BaseModel):
    """LLM-facing v2 claim schema.

    A role/skill/keyword is not globally positive just because it appears in a
    positive shot.  The claim must state the relation and source section.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: str
    entity_type: Literal["role", "skill", "keyword", "anti_pattern", "seniority"]
    relation: OntologyPredicate
    source_section: OntologySourceSection = "unknown"
    polarity: OntologyPolarity
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    is_target_boundary: bool = False
    surface_text: str = ""
    reason: str = ""


class ExtractedRoleSkillEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    skill: str
    relation: Literal["requires_skill", "supports_role"] = "supports_role"
    source_section: OntologySourceSection = "unknown"
    polarity: OntologyPolarity = "positive"
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""


class ShotOntologyExtraction(BaseModel):
    """Preferred state-of-the-art-ish extraction payload for one shot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[ExtractedOntologyClaim, ...] = ()
    role_skill_edges: tuple[ExtractedRoleSkillEdge, ...] = ()


class MaterializedOntologyTerms(BaseModel):
    """Compatibility projection for the existing ontology tables."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    positive_roles: tuple[str, ...] = ()
    negative_roles: tuple[str, ...] = ()
    positive_skills: tuple[str, ...] = ()
    negative_skills: tuple[str, ...] = ()
    seniority: tuple[str, ...] = ()
    anti_patterns: tuple[str, ...] = ()
    positive_keywords: tuple[tuple[str, int], ...] = ()
    negative_keywords: tuple[tuple[str, int], ...] = ()
    contextual_roles: tuple[str, ...] = ()
    contextual_skills: tuple[str, ...] = ()


class CompiledOntologyTerm(BaseModel):
    """LLM-compiled ontology decision for one canonical term.

    This is the semantic source of truth.  Legacy role/skill/keyword tables are
    only a projection from accepted compiled terms.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: str
    aliases: tuple[str, ...] = ()
    entity_type: Literal["role", "skill", "keyword", "anti_pattern", "seniority"]
    semantic_role: OntologySemanticRole
    polarity: OntologyPolarity
    scope: OntologyScope
    source_section: OntologySourceSection = "unknown"
    evidence_shot_ids: tuple[str, ...] = ()
    support_count: int = Field(default=0, ge=0)
    contrast_count: int = Field(default=0, ge=0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    accepted: bool = False
    reject_reason: str = ""
    rationale: str = ""


class CompiledOntologyRelation(BaseModel):
    """Weighted graph edge between compiled ontology terms."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    predicate: Literal[
        "requires",
        "supports",
        "excludes",
        "aliases",
        "contrasts_with",
        "specializes",
        "broader_than",
        "cooccurs_with",
    ]
    object: str
    polarity: OntologyPolarity = "contextual"
    evidence_shot_ids: tuple[str, ...] = ()
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = ""


class CompiledOntology(BaseModel):
    """Final profile-level ontology learned from the whole shot corpus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = ""
    terms: tuple[CompiledOntologyTerm, ...] = ()
    relations: tuple[CompiledOntologyRelation, ...] = ()
    warnings: tuple[str, ...] = ()


class OntologyTermStat(BaseModel):
    """Corpus-level explanation for a materialized ontology term."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: Literal["role", "skill", "keyword", "anti_pattern", "seniority"]
    canonical: str
    polarity: OntologyPolarity
    aliases: tuple[str, ...] = ()
    positive_count: int = 0
    negative_count: int = 0
    contextual_count: int = 0
    positive_weight: float = 0.0
    negative_weight: float = 0.0
    contextual_weight: float = 0.0
    recency_weight: float = 0.0
    section_weight: float = 0.0
    keyness: float = 0.0
    score: float = 0.0
    antonyms: tuple[str, ...] = ()
    related_terms: tuple[str, ...] = ()


class ShotOntologyGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_id: str
    shot_id: str
    source_type: str
    nodes: tuple[OntologyNode, ...] = ()
    edges: tuple[OntologyEdge, ...] = ()
    evidence: tuple[OntologyEvidence, ...] = ()
    materialized: MaterializedOntologyTerms = Field(default_factory=MaterializedOntologyTerms)
