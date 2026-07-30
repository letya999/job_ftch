"""LLM relevance is evidence production, never terminal routing."""

import pytest

from job_ftch.domain import (
    AssessedJob,
    MatchDecision,
    RelevanceEvidenceClassification,
    SearchProfile,
    WorkState,
)
from job_ftch.nodes.decision import DecisionNode
from job_ftch.nodes.llm_relevance_classification import (
    LLMRelevanceClassificationNode,
    LLMRelevanceEvidenceNode,
    _build_compact_evidence_prompt,
    _build_relevance_prompt,
)


class _Store:
    async def get_run_state(self, _key: str) -> str | None:
        return None

    async def set_run_state(self, _key: str, _value: str) -> None:
        return None


class _Catalog:
    profiles = [SearchProfile(profile_id="p1", target_roles=("Engineer",))]


@pytest.mark.asyncio
async def test_llm_result_is_typed_evidence_not_terminal_routing(make_job_record) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return type(
                "Result",
                (),
                {
                    "decision": "accept",
                    "confidence": 0.9,
                    "reasoning": "matched",
                    "matched_positive_aspects": [],
                    "mismatched_aspects": [],
                },
            )()

    item = make_job_record(relevance_score=0.8)
    out = await LLMRelevanceClassificationNode(
        llm=Provider(), store=_Store(), catalog=_Catalog(), high_threshold=0.99
    ).process(item)
    assert out.routing_decision is None
    assert out.metadata["_llm_relevance"]["decision"] == "accept"
    atom = out.metadata["evidence_atoms"][-1]
    assert atom["claim"] == "profile_relevance"
    assert atom["provenance"] == "llm"


@pytest.mark.asyncio
async def test_provider_failure_is_degradation_metadata(make_job_record) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            raise RuntimeError("quota")

    out = await LLMRelevanceClassificationNode(
        llm=Provider(), store=_Store(), catalog=_Catalog()
    ).process(make_job_record(relevance_score=0.3))
    assert out.routing_decision is None
    assert out.metadata["llm_relevance_degradation"] == "provider_unavailable"


@pytest.mark.asyncio
async def test_transient_provider_failure_does_not_open_circuit_after_one_item(
    make_job_record,
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, _prompt: str, model: type | None = None) -> object:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            return type(
                "Result",
                (),
                {
                    "decision": "accept",
                    "confidence": 0.9,
                    "reasoning": "matched",
                    "matched_positive_aspects": [],
                    "mismatched_aspects": [],
                },
            )()

    provider = Provider()
    node = LLMRelevanceClassificationNode(
        llm=provider,
        store=_Store(),
        catalog=_Catalog(),
        high_threshold=0.99,
    )

    first = await node.process(make_job_record(relevance_score=0.3))
    second = await node.process(make_job_record(relevance_score=0.3))

    assert first.metadata["llm_relevance_degradation"] == "provider_unavailable"
    assert second.metadata["_llm_relevance"]["decision"] == "accept"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_llm_reject_is_not_overridden_by_matching_title(make_job_record) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return type(
                "Result",
                (),
                {
                    "decision": "reject",
                    "confidence": 0.95,
                    "reasoning": "Responsibilities are not relevant.",
                    "matched_positive_aspects": [],
                    "mismatched_aspects": ["No relevant responsibilities"],
                },
            )()

    catalog = type(
        "Catalog",
        (),
        {
            "profiles": [
                SearchProfile(profile_id="p1", target_roles=("AI Project Manager",)),
            ]
        },
    )()
    item = make_job_record(title="AI Project Manager", relevance_score=0.4)

    out = await LLMRelevanceClassificationNode(
        llm=Provider(), store=_Store(), catalog=catalog, high_threshold=0.99
    ).process(item)

    assert out.metadata["_llm_relevance"]["decision"] == "reject"
    assert out.relevance_score == 0.4


def test_compiled_profile_brief_excludes_raw_shots_from_each_judge_prompt(make_job_record) -> None:
    profile = SearchProfile(
        profile_id="p1",
        target_roles=("Engineer",),
        positive_job_example_texts=("very long accepted vacancy text",),
        negative_job_example_texts=("very long rejected vacancy text",),
    )
    prompt = _build_relevance_prompt(
        make_job_record(title="AI Engineer", description="Builds product features."),
        profile,
        positive_jobs=profile.positive_job_example_texts,
        negative_jobs=profile.negative_job_example_texts,
        system_prompt_override="COMPILED BRIEF",
        include_raw_examples=False,
    )
    assert "COMPILED BRIEF" in prompt
    assert "very long accepted vacancy text" not in prompt
    assert "very long rejected vacancy text" not in prompt


@pytest.mark.asyncio
async def test_typed_graph_adapter_returns_assessed_job_with_llm_atom(make_job_record) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return type(
                "Result",
                (),
                {
                    "decision": "accept",
                    "confidence": 0.9,
                    "reasoning": "matched",
                    "matched_positive_aspects": [],
                    "mismatched_aspects": [],
                },
            )()

    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=_Catalog())
    node.configure_graph_params({"call_policy": "force_all"})
    assessed = AssessedJob(
        record=make_job_record(relevance_score=0.1),
        policy_version="test-policy",
    )

    out = await node.process(assessed)

    assert isinstance(out, AssessedJob)
    assert any(atom.producer == "llm_relevance" for atom in out.evidence)


def test_compact_prompt_numbers_responsibility_evidence(make_job_record) -> None:
    profile = SearchProfile(
        profile_id="p1",
        profile_description="Applied LLM engineer",
        target_roles=("LLM Engineer",),
        anti_preferences=("product management", "data engineering"),
    )
    prompt = _build_compact_evidence_prompt(
        make_job_record(
            title="AI Product Manager",
            description="Own the roadmap and coordinate engineers for an AI product.",
            responsibilities=("Own the roadmap", "Coordinate engineers"),
        ),
        profile,
        system_prompt_override="Target engineers who personally implement LLM systems.",
    )

    assert "[1] title: AI Product Manager" in prompt
    assert "] responsibility: Own the roadmap" in prompt
    assert "product, team name, or tooling is context only" in prompt
    assert "product management" in prompt


def test_compact_prompt_allows_unambiguous_target_title_without_duties(make_job_record) -> None:
    prompt = _build_compact_evidence_prompt(
        make_job_record(
            title="Senior Product Manager – AI & Game-Based Learning",
            description="Acme. Remote. Apply: @recruiter",
            responsibilities=(),
        ),
        SearchProfile(profile_id="product", target_roles=("Product Manager",)),
    )

    assert "unambiguous exact or close target title is sufficient positive evidence" in prompt
    assert "one of those roles or performs their core work" in prompt
    assert "remain the same target role family" in prompt
    assert "merely uses a tool associated with the target work is adjacent" in prompt


@pytest.mark.asyncio
async def test_compact_target_title_accepts_when_responsibilities_are_missing(
    make_job_record,
) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="unknown",
                positive_evidence_ids=(1,),
                negative_evidence_ids=(2,),
            )

    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=_Catalog())
    node.configure_graph_params(
        {"call_policy": "force_all", "classification_mode": "compact_evidence"}
    )
    assessed = AssessedJob(record=make_job_record(post_type="unknown"), policy_version="test")

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is MatchDecision.ACCEPT
    assert result.assessed_job.record.post_type.value == "job_posting"


@pytest.mark.asyncio
async def test_compact_exact_target_title_does_not_accept_adjacent_when_duties_are_missing(
    make_job_record,
) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="unknown",
                positive_evidence_ids=(1, 2),
                negative_evidence_ids=(3, 4),
            )

    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("AI Product Manager",))]},
    )()
    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {"call_policy": "force_all", "classification_mode": "compact_evidence"}
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="Product Manager (AI Apps & Subscriptions)",
            description="Acme is hiring. Remote. Apply via recruiter.",
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is MatchDecision.REVIEW


@pytest.mark.asyncio
async def test_compact_clean_adjacent_support_accepts_with_supporting_duties(
    make_job_record,
) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="support",
                positive_evidence_ids=(1, 2),
            )

    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("AI Product Manager",))]},
    )()
    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {"call_policy": "force_all", "classification_mode": "compact_evidence"}
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="ML Product Manager",
            description="Own automation products.",
            responsibilities=("Own automation products.",),
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_compact_adjacent_support_with_negative_evidence_stays_review(
    make_job_record,
) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="support",
                positive_evidence_ids=(1, 2),
                negative_evidence_ids=(3,),
            )

    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("AI Product Manager",))]},
    )()
    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {"call_policy": "force_all", "classification_mode": "compact_evidence"}
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="ML Product Manager",
            description="Own automation products.",
            responsibilities=("Own automation products.",),
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is MatchDecision.REVIEW


@pytest.mark.asyncio
async def test_compact_adjacent_generic_title_is_not_promoted(make_job_record) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="unknown",
                positive_evidence_ids=(1,),
            )

    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("AI Product Manager",))]},
    )()
    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {"call_policy": "force_all", "classification_mode": "compact_evidence"}
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="Product Manager",
            description="Travel marketplace is hiring. Apply via recruiter.",
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is not MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_compact_conflicting_accept_runs_precision_confirmation(make_job_record) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, _prompt: str, model: type | None = None) -> object:
            self.calls += 1
            if self.calls == 1:
                return RelevanceEvidenceClassification(
                    is_job="yes",
                    role_relation="target",
                    responsibility_fit="support",
                    positive_evidence_ids=(1, 4),
                    negative_evidence_ids=(2, 3),
                )
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="contradict",
                positive_evidence_ids=(1,),
                negative_evidence_ids=(2,),
            )

    provider = Provider()
    node = LLMRelevanceEvidenceNode(llm=provider, store=_Store(), catalog=_Catalog())
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_precision_confirmations": 1,
        }
    )
    assessed = AssessedJob(
        record=make_job_record(title="Technical Cofounder"),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))
    evidence = result.assessed_job.record.metadata["_llm_relevance"]

    assert provider.calls == 2
    assert evidence["precision_confirmation"]["role_relation"] == "adjacent"
    assert result.routing_decision is MatchDecision.REVIEW


@pytest.mark.asyncio
async def test_compact_precision_confirmation_can_keep_target_accept(make_job_record) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, _prompt: str, model: type | None = None) -> object:
            self.calls += 1
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="support",
                positive_evidence_ids=(1, 4),
                negative_evidence_ids=(2,) if self.calls == 1 else (),
            )

    provider = Provider()
    node = LLMRelevanceEvidenceNode(llm=provider, store=_Store(), catalog=_Catalog())
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_precision_confirmations": 1,
        }
    )
    assessed = AssessedJob(
        record=make_job_record(title="Technical Cofounder"),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert provider.calls == 2
    assert result.routing_decision is MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_compact_precision_confirmation_skips_exact_target_title(make_job_record) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, _prompt: str, model: type | None = None) -> object:
            self.calls += 1
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="support",
                positive_evidence_ids=(1,),
                negative_evidence_ids=(2, 3),
            )

    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("AI Engineer",))]},
    )()
    provider = Provider()
    node = LLMRelevanceEvidenceNode(llm=provider, store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_precision_confirmations": 1,
        }
    )
    assessed = AssessedJob(
        record=make_job_record(title="Junior AI Engineer"),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert provider.calls == 1
    assert result.routing_decision is MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_compact_ambiguity_resolver_promotes_cited_target_work(make_job_record) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, _prompt: str, model: type | None = None) -> object:
            self.calls += 1
            if self.calls == 1:
                return RelevanceEvidenceClassification(
                    is_job="yes",
                    role_relation="adjacent",
                    responsibility_fit="support",
                    positive_evidence_ids=(1,),
                    negative_evidence_ids=(2,),
                )
            assert "FULL VACANCY EVIDENCE" in _prompt
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="support",
                positive_evidence_ids=(1, 2),
            )

    provider = Provider()
    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("AI Architect",))]},
    )()
    node = LLMRelevanceEvidenceNode(llm=provider, store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_ambiguity_resolutions": 1,
        }
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="Presale specialist",
            description="Design LLM and RAG architectures, then lead customer integrations.",
            responsibilities=(),
            metadata={
                "original_posting_text": (
                    "Presale specialist. Design LLM and RAG architectures and lead customer integrations."
                )
            },
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert provider.calls == 2
    assert result.routing_decision is MatchDecision.ACCEPT
    evidence = result.assessed_job.record.metadata["_llm_relevance"]
    assert evidence["primary"]["role_relation"] == "adjacent"
    assert evidence["ambiguity_resolution"]["role_relation"] == "target"


@pytest.mark.asyncio
async def test_compact_ambiguity_resolver_keeps_generic_ai_role_unpublished(
    make_job_record,
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, _prompt: str, model: type | None = None) -> object:
            self.calls += 1
            if self.calls == 1:
                return RelevanceEvidenceClassification(
                    is_job="yes",
                    role_relation="adjacent",
                    responsibility_fit="support",
                    positive_evidence_ids=(1,),
                )
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="contradict",
                positive_evidence_ids=(1,),
                negative_evidence_ids=(2,),
            )

    provider = Provider()
    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("AI Architect",))]},
    )()
    node = LLMRelevanceEvidenceNode(llm=provider, store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_ambiguity_resolutions": 1,
        }
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="Product Manager",
            description="Own a marketplace roadmap; the company uses AI tools.",
            responsibilities=(),
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert provider.calls == 2
    assert result.routing_decision is not MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_compact_target_claim_does_not_promote_generic_title_without_duties(
    make_job_record,
) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="unknown",
                positive_evidence_ids=(1,),
            )

    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("AI Product Manager",))]},
    )()
    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {"call_policy": "force_all", "classification_mode": "compact_evidence"}
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="Product Manager",
            description="Travel marketplace is hiring. Apply via recruiter.",
            responsibilities=(),
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is MatchDecision.REVIEW
    assert result.work_state is WorkState.TERMINAL
    assert result.reasons[-1] == "profile_relevance_uncertain"


@pytest.mark.asyncio
async def test_compact_russian_ml_product_title_is_an_exact_target_without_duties(
    make_job_record,
) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="unknown",
                positive_evidence_ids=(1,),
                negative_evidence_ids=(2,),
            )

    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("AI Product Manager",))]},
    )()
    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {"call_policy": "force_all", "classification_mode": "compact_evidence"}
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="Миддл – Лид ML-продакт-менеджеры",
            description="Т-Банк. Ищет Владимир, его пост на LinkedIn.",
            responsibilities=(),
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_compact_unknown_duties_cannot_turn_ai_link_chatter_into_job(
    make_job_record,
) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="unknown",
                positive_evidence_ids=(1,),
            )

    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=_Catalog())
    node.configure_graph_params(
        {"call_policy": "force_all", "classification_mode": "compact_evidence"}
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="GPT-5.4 release",
            description="https://example.com/introducing-gpt-5-4 — хороший релиз",
            metadata={
                "original_posting_text": ("https://example.com/introducing-gpt-5-4 — хороший релиз")
            },
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is not MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_compact_evidence_has_no_self_reported_confidence_or_reasoning(
    make_job_record,
) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            assert model is RelevanceEvidenceClassification
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="support",
                positive_evidence_ids=(1, 2),
            )

    node = LLMRelevanceClassificationNode(
        llm=Provider(),
        store=_Store(),
        catalog=_Catalog(),
        high_threshold=0.99,
    )
    node.configure_graph_params({"classification_mode": "compact_evidence"})

    out = await node.process(
        make_job_record(
            title="LLM Engineer",
            description="Build RAG services.",
            responsibilities=("Build RAG services",),
            relevance_score=0.8,
        )
    )

    trace = out.metadata["_llm_relevance"]
    assert trace["decision"] == "accept"
    assert trace["primary"] == {
        "is_job": "yes",
        "role_relation": "target",
        "responsibility_fit": "support",
        "positive_evidence_ids": [1, 2],
        "negative_evidence_ids": [],
    }
    assert trace["ambiguity_resolution"] is None
    assert trace["prompt_variant"] == "profile_default"
    assert trace["classification_mode"] == "compact_evidence"
    atom = out.metadata["evidence_atoms"][-1]
    assert atom["polarity"] == "supports"
    assert atom["strength"] == 1.0
    assert atom["evidence_ref"] == "llm:relevance_evidence:1,2"
    jobness_atom = out.metadata["evidence_atoms"][-2]
    assert jobness_atom["claim"] == "is_job"
    assert jobness_atom["polarity"] == "supports"
    assert jobness_atom["evidence_ref"] == "llm:jobness_evidence:1,2"


@pytest.mark.asyncio
async def test_compact_adjacent_role_is_negative_evidence(make_job_record) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="contradict",
                negative_evidence_ids=(1, 3),
            )

    node = LLMRelevanceClassificationNode(
        llm=Provider(), store=_Store(), catalog=_Catalog(), high_threshold=0.99
    )
    node.configure_graph_params({"classification_mode": "compact_evidence"})

    out = await node.process(make_job_record(relevance_score=0.8))

    assert out.metadata["_llm_relevance"]["decision"] == "reject"
    assert out.metadata["evidence_atoms"][-1]["polarity"] == "contradicts"
    assert len(out.metadata["evidence_atoms"]) == 1
    assert out.relevance_score == 0.8


@pytest.mark.asyncio
async def test_compact_jobness_evidence_can_resolve_unknown_cheap_jobness(make_job_record) -> None:
    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="support",
                positive_evidence_ids=(1,),
            )

    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=_Catalog())
    node.configure_graph_params(
        {"call_policy": "force_all", "classification_mode": "compact_evidence"}
    )
    assessed = AssessedJob(record=make_job_record(relevance_score=0.1), policy_version="test")

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_cache_key_tracks_prompt_graph_and_model(make_job_record) -> None:
    class Store:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def get_run_state(self, key: str) -> str | None:
            return self.values.get(key)

        async def set_run_state(self, key: str, value: str) -> None:
            self.values[key] = value

    class Provider:
        def __init__(self, model_id: str) -> None:
            self.model_id = model_id
            self.calls = 0

        async def classify(self, _prompt: str, model: type | None = None) -> object:
            self.calls += 1
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="support",
                positive_evidence_ids=(1,),
            )

    async def classify(
        store: Store,
        provider: Provider,
        *,
        graph_hash: str,
        description: str,
    ) -> None:
        node = LLMRelevanceClassificationNode(
            llm=provider,
            store=store,
            catalog=_Catalog(),
            graph_hash=graph_hash,
        )
        node.configure_graph_params(
            {"call_policy": "force_all", "classification_mode": "compact_evidence"}
        )
        await node.process(make_job_record(description=description))

    store = Store()
    first = Provider("model-a")
    await classify(store, first, graph_hash="graph-a", description="Build RAG systems")
    await classify(store, first, graph_hash="graph-a", description="Build RAG systems")
    assert first.calls == 1

    await classify(store, first, graph_hash="graph-a", description="Sell insurance")
    await classify(store, first, graph_hash="graph-b", description="Build RAG systems")
    second = Provider("model-b")
    await classify(store, second, graph_hash="graph-a", description="Build RAG systems")

    assert first.calls == 3
    assert second.calls == 1
    assert len(store.values) == 4
    assert all(key.startswith("relevance-v7:") for key in store.values)


# Roles observed in the 2026-07-24 live 14-source run that a human confirmed as
# on-profile. None of them matches a target role by title, so gating the
# ambiguity resolver on a title match silently drops the whole class.
_LIVE_NON_TITLE_MATCH_TARGETS = (
    "Менеджер проекта по реализации AI-агента",
    "CJE в команду развития ИИ",
    "Presale-архитектор ИИ-платформы",
    "Специалист по безопасности генеративного ИИ",
    "Ведущий аналитик по разработке ИИ-агентов",
)

# Roles from the same run that a human confirmed as off-profile: generic ML on a
# search/navigation/recsys product, with no ownership of an LLM/agent system.
_LIVE_GENERIC_ML_NEGATIVES = (
    "ML-разработчик в команду геопоиска",
    "Лид в команду ML-технологий навигации",
    "Стажер направления Data Science",
)


def _agent_profile_catalog() -> object:
    return type(
        "Catalog",
        (),
        {
            "profiles": [
                SearchProfile(
                    profile_id="p1",
                    target_roles=("AI Engineer", "LLM Engineer"),
                    profile_description="Build and integrate LLM, RAG and agentic systems.",
                )
            ]
        },
    )()


@pytest.mark.asyncio
@pytest.mark.parametrize("title", _LIVE_NON_TITLE_MATCH_TARGETS)
async def test_compact_resolver_runs_for_on_profile_roles_without_a_matching_title(
    make_job_record, title: str
) -> None:
    """A non-matching title must not stop the resolver from seeing the duties.

    Gating the second pass on the title collapsed live recall from 0.97 to 0.61,
    because on-profile agent work is routinely advertised under analyst, presale,
    project-manager and security titles.
    """

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, _prompt: str, model: type | None = None) -> object:
            self.calls += 1
            if self.calls == 1:
                return RelevanceEvidenceClassification(
                    is_job="yes",
                    role_relation="adjacent",
                    responsibility_fit="support",
                    positive_evidence_ids=(1,),
                )
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="support",
                positive_evidence_ids=(1, 2),
            )

    provider = Provider()
    node = LLMRelevanceEvidenceNode(llm=provider, store=_Store(), catalog=_agent_profile_catalog())
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_ambiguity_resolutions": 1,
        }
    )
    assessed = AssessedJob(
        record=make_job_record(
            title=title,
            description=(
                "Отвечает за разработку и внедрение ИИ-агента: проектирование сценариев, "
                "интеграция LLM, оценка качества ответов."
            ),
            responsibilities=(),
            metadata={
                "original_posting_text": (
                    f"{title}. Разработка и внедрение ИИ-агента, интеграция LLM, "
                    "проектирование агентных сценариев."
                )
            },
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert provider.calls == 2, "the ambiguity resolver must run without a title match"
    assert result.routing_decision is MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_compact_clean_adjacent_unknown_accepts_cited_positive_evidence(
    make_job_record,
) -> None:
    """Broad AI-automation profiles may accept adjacent roles with clean cited evidence."""

    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="unknown",
                positive_evidence_ids=(1, 4),
                negative_evidence_ids=(),
            )

    node = LLMRelevanceEvidenceNode(
        llm=Provider(), store=_Store(), catalog=_agent_profile_catalog()
    )
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_ambiguity_resolutions": 0,
        }
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="ML-разработчик в команду агентного поиска",
            description=(
                "Команда развивает поиск как инструмент для AI-агентов. Нужно улучшать "
                "оценку качества, retrieval и интеграцию с LLM-сценариями."
            ),
            responsibilities=(),
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_compact_adjacent_unknown_negative_evidence_blocks_accept(make_job_record) -> None:
    """The recall boost is still evidence-bound and does not ignore contradictions."""

    class Provider:
        async def classify(self, _prompt: str, model: type | None = None) -> object:
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="unknown",
                positive_evidence_ids=(1,),
                negative_evidence_ids=(3,),
            )

    node = LLMRelevanceEvidenceNode(
        llm=Provider(), store=_Store(), catalog=_agent_profile_catalog()
    )
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_ambiguity_resolutions": 0,
        }
    )
    assessed = AssessedJob(
        record=make_job_record(
            title="ML-разработчик ранжирования",
            description="Классическое ML-ранжирование, A/B тесты и метрики качества поиска.",
            responsibilities=(),
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is not MatchDecision.ACCEPT


@pytest.mark.asyncio
@pytest.mark.parametrize("title", _LIVE_GENERIC_ML_NEGATIVES)
async def test_compact_generic_ml_role_stays_unpublished(make_job_record, title: str) -> None:
    """Recovering recall must not turn generic ML products into accepts."""

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, _prompt: str, model: type | None = None) -> object:
            self.calls += 1
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="adjacent",
                responsibility_fit="unknown",
                positive_evidence_ids=(),
                negative_evidence_ids=(1,),
            )

    provider = Provider()
    node = LLMRelevanceEvidenceNode(llm=provider, store=_Store(), catalog=_agent_profile_catalog())
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_ambiguity_resolutions": 1,
        }
    )
    assessed = AssessedJob(
        record=make_job_record(
            title=title,
            description="Обучение ранжирующих моделей, метрики качества поиска, A/B эксперименты.",
            responsibilities=(),
            metadata={
                "original_posting_text": (
                    f"{title}. Обучение ранжирующих моделей и улучшение метрик поиска."
                )
            },
        ),
        policy_version="test",
    )

    result = await DecisionNode().process(await node.process(assessed))

    assert result.routing_decision is not MatchDecision.ACCEPT


# Naming one field's technologies in the shared prompt biases every other profile that
# reuses this node. Domain guidance belongs in the compiled brief, which is derived from
# the profile's own shots.
_DOMAIN_LITERALS = (
    "llm",
    "rag",
    "nlp",
    "ai assistant",
    "agentic",
    "ai/ml",
    "n8n",
    "machine learning",
)


def _domain_literals_in(text: str) -> list[str]:
    lowered = text.casefold()
    return [literal for literal in _DOMAIN_LITERALS if literal in lowered]


def test_static_compact_task_rules_name_no_domain(make_job_record) -> None:
    from job_ftch.nodes.llm_relevance_classification import _DOMAIN_NEUTRAL_TASK_RULES

    assert _domain_literals_in(_DOMAIN_NEUTRAL_TASK_RULES) == []


def test_compact_prompt_domain_words_come_only_from_the_profile(make_job_record) -> None:
    """A non-technical profile must not receive AI/LLM instructions."""
    prompt = _build_compact_evidence_prompt(
        make_job_record(
            title="Ward nurse",
            description="Care for post-operative patients on a surgical ward.",
            responsibilities=("Administer medication",),
        ),
        SearchProfile(profile_id="nursing", target_roles=("Registered Nurse",)),
    )

    assert _domain_literals_in(prompt) == []
    assert "Registered Nurse" in prompt


def test_ambiguity_prompt_names_no_domain_and_carries_the_brief(make_job_record) -> None:
    from job_ftch.nodes.llm_relevance_classification import _build_ambiguity_resolution_prompt

    item = make_job_record(
        title="Ward nurse",
        description="Care for post-operative patients.",
        responsibilities=(),
    )
    profile = SearchProfile(profile_id="nursing", target_roles=("Registered Nurse",))

    without_brief = _build_ambiguity_resolution_prompt(item, profile)
    assert _domain_literals_in(without_brief) == []

    with_brief = _build_ambiguity_resolution_prompt(
        item, profile, system_prompt_override="CORE WORK: direct bedside patient care."
    )
    assert "CORE WORK: direct bedside patient care." in with_brief


@pytest.mark.asyncio
async def test_ambiguity_resolution_receives_the_compiled_brief(make_job_record) -> None:
    """The second pass decides the same boundary, so it needs the same brief."""
    seen: list[str] = []

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, prompt: str, model: type | None = None) -> object:
            self.calls += 1
            seen.append(prompt)
            if self.calls == 1:
                return RelevanceEvidenceClassification(
                    is_job="yes",
                    role_relation="adjacent",
                    responsibility_fit="support",
                    positive_evidence_ids=(1,),
                )
            return RelevanceEvidenceClassification(
                is_job="yes",
                role_relation="target",
                responsibility_fit="support",
                positive_evidence_ids=(1,),
            )

    catalog = type(
        "Catalog",
        (),
        {"profiles": [SearchProfile(profile_id="p1", target_roles=("Registered Nurse",))]},
    )()
    node = LLMRelevanceEvidenceNode(llm=Provider(), store=_Store(), catalog=catalog)
    node.configure_graph_params(
        {
            "call_policy": "force_all",
            "classification_mode": "compact_evidence",
            "max_ambiguity_resolutions": 1,
        }
    )
    node._classifier._relevance_prompts = {"p1": "CORE WORK: direct bedside patient care."}

    await node.process(
        AssessedJob(
            record=make_job_record(
                title="Ward coordinator",
                description="Coordinate bedside care rotas.",
                responsibilities=(),
                metadata={"original_posting_text": "Ward coordinator. Coordinate bedside care."},
            ),
            policy_version="test",
        )
    )

    assert len(seen) == 2, "the resolver must run"
    assert "CORE WORK: direct bedside patient care." in seen[1]
