"""Build ontology graph records from compiled ontology decisions."""

from __future__ import annotations

import hashlib

from job_ftch.domain import (
    CompiledOntology,
    MaterializedOntologyTerms,
    OntologyEdge,
    OntologyEvidence,
    OntologyNode,
    ShotOntologyGraph,
)

_ASSERT_PREDICATE = {
    "role": "asserts_role",
    "skill": "asserts_skill",
    "keyword": "asserts_keyword",
    "anti_pattern": "asserts_anti_pattern",
    "seniority": "asserts_seniority",
}

_RELATION_PREDICATE = {
    "requires": "requires_skill",
    "supports": "supports_role",
    "excludes": "excludes_skill",
    "aliases": "aliases",
    "contrasts_with": "excludes_skill",
    "specializes": "supports_role",
    "broader_than": "supports_role",
    "cooccurs_with": "supports_role",
}


def normalize_ontology_term(value: str) -> str:
    value = value.strip().casefold()
    value = value.replace("_", " ").replace("-", " ")
    value = "".join(
        char if char.isalnum() or char.isspace() or char in ".+#/&" else " " for char in value
    )
    return " ".join(value.split())


def _node_id(kind: str, canonical: str) -> str:
    digest = hashlib.sha1(f"{kind}:{canonical}".encode(), usedforsecurity=False).hexdigest()[:16]
    return f"{kind}:{digest}"


def _edge_id(subject: str, predicate: str, obj: str, polarity: str) -> str:
    digest = hashlib.sha1(
        f"{subject}:{predicate}:{obj}:{polarity}".encode(), usedforsecurity=False
    ).hexdigest()[:16]
    return f"edge:{digest}"


def build_ontology_graph_from_compiled(
    *,
    ontology: CompiledOntology,
    graph_id: str,
    shot_id: str,
    source_type: str = "compiled_profile",
    lang: str = "mixed",
    model: str | None = None,
    prompt_hash: str | None = None,
    materialized: MaterializedOntologyTerms | None = None,
) -> ShotOntologyGraph:
    """Create a persistence graph from already-decided compiled terms."""

    profile_id = _node_id("profile", graph_id)
    nodes: dict[str, OntologyNode] = {
        profile_id: OntologyNode(
            node_id=profile_id,
            kind="profile",
            canonical=graph_id,
            display=ontology.summary or graph_id,
            lang=lang,
            attrs={"source": "compiled_ontology"},
        )
    }
    edges: list[OntologyEdge] = []
    evidence: list[OntologyEvidence] = []

    for term in ontology.terms:
        canonical = normalize_ontology_term(term.canonical)
        if not canonical:
            continue
        node_id = _node_id(term.entity_type, canonical)
        nodes[node_id] = OntologyNode(
            node_id=node_id,
            kind=term.entity_type,
            canonical=canonical,
            display=term.canonical,
            lang=lang,
            attrs={
                "semantic_role": term.semantic_role,
                "scope": term.scope,
                "accepted": term.accepted,
                "reject_reason": term.reject_reason,
                "aliases": list(term.aliases),
            },
        )
        edge_id = _edge_id(profile_id, "asserts_" + term.entity_type, node_id, term.polarity)
        edges.append(
            OntologyEdge(
                edge_id=edge_id,
                subject_node_id=profile_id,
                predicate=_ASSERT_PREDICATE[term.entity_type],  # type: ignore[arg-type]
                object_node_id=node_id,
                polarity=term.polarity,
                weight=term.weight,
                confidence=term.confidence,
                attrs={
                    "semantic_role": term.semantic_role,
                    "scope": term.scope,
                    "accepted": term.accepted,
                    "support_count": term.support_count,
                    "contrast_count": term.contrast_count,
                },
            )
        )
        for evidence_shot_id in term.evidence_shot_ids:
            evidence.append(
                OntologyEvidence(
                    evidence_id=hashlib.sha1(
                        f"{edge_id}:{evidence_shot_id}".encode(),
                        usedforsecurity=False,
                    ).hexdigest(),
                    edge_id=edge_id,
                    source_shot_id=evidence_shot_id,
                    source_type=source_type,
                    source_section=term.source_section,
                    extraction_confidence=term.confidence,
                    model=model,
                    prompt_hash=prompt_hash,
                )
            )

    for relation in ontology.relations:
        subject = _node_id("term", normalize_ontology_term(relation.subject))
        obj = _node_id("term", normalize_ontology_term(relation.object))
        if subject not in nodes:
            nodes[subject] = OntologyNode(
                node_id=subject,
                kind="claim",
                canonical=normalize_ontology_term(relation.subject),
                lang=lang,
            )
        if obj not in nodes:
            nodes[obj] = OntologyNode(
                node_id=obj,
                kind="claim",
                canonical=normalize_ontology_term(relation.object),
                lang=lang,
            )
        edge_id = _edge_id(subject, relation.predicate, obj, relation.polarity)
        edges.append(
            OntologyEdge(
                edge_id=edge_id,
                subject_node_id=subject,
                predicate=_RELATION_PREDICATE[relation.predicate],  # type: ignore[arg-type]
                object_node_id=obj,
                polarity=relation.polarity,
                weight=relation.weight,
                confidence=relation.confidence,
                attrs={"compiled_predicate": relation.predicate},
            )
        )

    return ShotOntologyGraph(
        graph_id=graph_id,
        shot_id=shot_id,
        source_type=source_type,
        nodes=tuple(nodes.values()),
        edges=tuple(edges),
        evidence=tuple(evidence),
        materialized=materialized or MaterializedOntologyTerms(),
    )
