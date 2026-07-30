from __future__ import annotations

from pathlib import Path

from job_ftch.application.ontology_compiler import (
    LabeledOntologyShot,
    OntologyCandidateChunk,
    _candidate_chunks_for_compile,
    _restore_projection_from_candidates,
    load_ontology_compiler_prompts,
    materialize_compiled_ontology,
    sanitize_compiled_ontology,
)
from job_ftch.application.ontology_graph_builder import build_ontology_graph_from_compiled
from job_ftch.domain import CompiledOntology, CompiledOntologyRelation, CompiledOntologyTerm


def test_prompt_config_loads_from_file() -> None:
    prompts = load_ontology_compiler_prompts(Path("config/prompts/ontology_compiler_v2.yaml"))

    assert prompts.schema_version == 1
    assert "{shots_json}" in prompts.candidate_user_template
    assert "{candidates_json}" in prompts.compile_user_template


def test_materialization_uses_compiled_semantics_without_code_reclassification() -> None:
    ontology = CompiledOntology(
        terms=(
            CompiledOntologyTerm(
                canonical="product manager",
                entity_type="role",
                semantic_role="past_role",
                polarity="contextual",
                scope="past",
                evidence_shot_ids=("resume-1",),
                support_count=1,
                confidence=0.9,
                weight=0.9,
                accepted=False,
                reject_reason="past role, not current or desired",
            ),
            CompiledOntologyTerm(
                canonical="medical imaging researcher",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                scope="target",
                evidence_shot_ids=("job-1",),
                support_count=2,
                confidence=0.88,
                weight=0.92,
                accepted=True,
            ),
            CompiledOntologyTerm(
                canonical="statistics",
                entity_type="skill",
                semantic_role="background_skill",
                polarity="contextual",
                scope="background",
                evidence_shot_ids=("job-1",),
                support_count=1,
                confidence=0.8,
                weight=0.4,
                accepted=True,
            ),
        )
    )

    materialized, stats = materialize_compiled_ontology(sanitize_compiled_ontology(ontology))

    assert materialized.positive_roles == ("medical imaging researcher",)
    assert "product manager" not in materialized.positive_roles
    assert "statistics" not in materialized.positive_skills
    assert any(stat.canonical == "product manager" and not stat.positive_count for stat in stats)


def test_sanitizer_rejects_accepted_terms_without_evidence_and_overlap() -> None:
    ontology = CompiledOntology(
        terms=(
            CompiledOntologyTerm(
                canonical="domain specialist",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                scope="target",
                accepted=True,
            ),
            CompiledOntologyTerm(
                canonical="domain specialist",
                entity_type="role",
                semantic_role="anti_role",
                polarity="negative",
                scope="anti",
                evidence_shot_ids=("neg-1",),
                accepted=True,
            ),
        )
    )

    sanitized = sanitize_compiled_ontology(ontology)
    materialized, _stats = materialize_compiled_ontology(sanitized)

    assert materialized.positive_roles == ()
    assert materialized.negative_roles == ("domain specialist",)
    assert not set(materialized.positive_roles) & set(materialized.negative_roles)


def test_graph_builder_persists_compiled_terms_and_relations() -> None:
    ontology = CompiledOntology(
        summary="target profile",
        terms=(
            CompiledOntologyTerm(
                canonical="automation architect",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                scope="target",
                evidence_shot_ids=("job-1",),
                accepted=True,
                confidence=0.9,
                weight=0.9,
            ),
            CompiledOntologyTerm(
                canonical="workflow orchestration",
                entity_type="skill",
                semantic_role="supporting_skill",
                polarity="positive",
                scope="supporting",
                evidence_shot_ids=("job-1",),
                accepted=True,
                confidence=0.8,
                weight=0.7,
            ),
        ),
        relations=(
            CompiledOntologyRelation(
                subject="automation architect",
                predicate="requires",
                object="workflow orchestration",
                polarity="positive",
                evidence_shot_ids=("job-1",),
                confidence=0.8,
                weight=0.7,
            ),
        ),
    )
    materialized, _stats = materialize_compiled_ontology(ontology)

    graph = build_ontology_graph_from_compiled(
        ontology=ontology,
        graph_id="compiled:test",
        shot_id="corpus-1",
        materialized=materialized,
    )

    assert any(node.canonical == "automation architect" for node in graph.nodes)
    assert any(edge.attrs.get("compiled_predicate") == "requires" for edge in graph.edges)
    assert graph.materialized.positive_roles == ("automation architect",)


def test_candidate_restore_keeps_specific_roles_without_skill_water() -> None:
    shots = (
        LabeledOntologyShot(shot_id="pos-1", kind="positive_job", text="AI Automation Engineer"),
        LabeledOntologyShot(shot_id="pos-2", kind="positive_job", text="AI Product Manager"),
    )
    ontology = CompiledOntology(
        terms=(
            CompiledOntologyTerm(
                canonical="ai product manager",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                scope="target",
                evidence_shot_ids=("pos-2",),
                accepted=True,
            ),
        )
    )
    candidates = (
        OntologyCandidateChunk(
            terms=(
                CompiledOntologyTerm(
                    canonical="AI Automation Engineer",
                    entity_type="role",
                    semantic_role="target_role",
                    polarity="positive",
                    scope="target",
                    source_section="title",
                    evidence_shot_ids=("pos-1",),
                    support_count=1,
                    confidence=0.9,
                    weight=0.8,
                ),
                CompiledOntologyTerm(
                    canonical="Product Manager",
                    entity_type="role",
                    semantic_role="target_role",
                    polarity="positive",
                    scope="target",
                    source_section="title",
                    evidence_shot_ids=("pos-2",),
                    support_count=1,
                    confidence=0.9,
                    weight=0.8,
                ),
                CompiledOntologyTerm(
                    canonical="Docker",
                    entity_type="skill",
                    semantic_role="target_skill",
                    polarity="positive",
                    scope="target",
                    source_section="requirements",
                    evidence_shot_ids=("pos-1",),
                    support_count=1,
                    confidence=0.95,
                    weight=0.9,
                ),
            ),
            relations=(
                CompiledOntologyRelation(
                    subject="AI Automation Engineer",
                    predicate="requires",
                    object="Workflow Orchestration",
                    polarity="positive",
                    evidence_shot_ids=("pos-1",),
                    confidence=0.9,
                    weight=0.8,
                ),
            ),
        ),
    )

    restored = sanitize_compiled_ontology(
        _restore_projection_from_candidates(ontology, candidates, shots)
    )
    materialized, _stats = materialize_compiled_ontology(restored)

    assert "ai automation engineer" in materialized.positive_roles
    assert "product manager" not in materialized.positive_roles
    assert "docker" in materialized.positive_skills
    assert "workflow orchestration" in materialized.positive_skills


def test_compile_subset_keeps_boundary_terms_without_full_tool_tail() -> None:
    chunk = OntologyCandidateChunk(
        terms=(
            CompiledOntologyTerm(
                canonical="mcp",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                scope="target",
                evidence_shot_ids=("pos-1",),
                support_count=1,
                confidence=0.9,
                weight=0.8,
            ),
            CompiledOntologyTerm(
                canonical="ai engineer",
                entity_type="role",
                semantic_role="target_role",
                polarity="positive",
                scope="target",
                evidence_shot_ids=("pos-1",),
                support_count=1,
                confidence=0.9,
                weight=0.8,
            ),
            CompiledOntologyTerm(
                canonical="no agent experience",
                entity_type="keyword",
                semantic_role="negative_keyword",
                polarity="negative",
                scope="anti",
                evidence_shot_ids=("neg-1",),
                support_count=1,
                confidence=0.9,
                weight=0.8,
            ),
        )
    )

    compact = _candidate_chunks_for_compile((chunk,))
    terms = {term.canonical for term in compact[0].terms}

    assert "mcp" not in terms
    assert "ai engineer" in terms
    assert "no agent experience" in terms


def test_negative_candidate_projection_preserves_contrast_without_overlap() -> None:
    shots = (
        LabeledOntologyShot(shot_id="pos-1", kind="positive_job", text="RAG engineer"),
        LabeledOntologyShot(shot_id="neg-1", kind="negative_job", text="No RAG experience"),
    )
    chunk = OntologyCandidateChunk(
        terms=(
            CompiledOntologyTerm(
                canonical="rag",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="positive",
                scope="target",
                source_section="requirements",
                evidence_shot_ids=("pos-1",),
                support_count=1,
                confidence=0.9,
                weight=0.8,
            ),
            CompiledOntologyTerm(
                canonical="rag",
                entity_type="skill",
                semantic_role="target_skill",
                polarity="negative",
                scope="anti",
                source_section="requirements",
                evidence_shot_ids=("neg-1",),
                support_count=1,
                confidence=0.9,
                weight=0.8,
            ),
        )
    )

    restored = _restore_projection_from_candidates(CompiledOntology(), (chunk,), shots)
    materialized, _stats = materialize_compiled_ontology(sanitize_compiled_ontology(restored))

    assert "rag" in materialized.positive_skills
    assert "rag" not in materialized.negative_skills
    assert "insufficient rag" in materialized.anti_patterns
    assert not set(materialized.positive_keywords) & set(materialized.negative_keywords)
