"""Node implementations — each file implements the Node Protocol."""

from job_ftch.nodes.accept_template_presentation import AcceptTemplatePresentationNode
from job_ftch.nodes.aggregation import JobAggregationNode
from job_ftch.nodes.candidate_segmentation import CandidateSegmentationNode
from job_ftch.nodes.completeness_gate import CompletenessGateNode
from job_ftch.nodes.decision import DecisionNode, DecisionPolicy
from job_ftch.nodes.decision_extraction import DecisionExtractionNode
from job_ftch.nodes.dedup import DedupNode
from job_ftch.nodes.embedding import EmbeddingNode
from job_ftch.nodes.evidence_decision import EvidenceDecisionNode
from job_ftch.nodes.evidence_fanout import EvidenceFanOutNode
from job_ftch.nodes.extraction import ExtractionNode
from job_ftch.nodes.extraction_validation import ExtractionValidationNode
from job_ftch.nodes.full_extraction import FullExtractionNode
from job_ftch.nodes.garbage_filter import GarbageFilterNode
from job_ftch.nodes.hard_filter import HardFilterNode
from job_ftch.nodes.job_normalization import (
    CompensationParsingNode,
    LocationWorkModeNormalizationNode,
    SkillNormalizationNode,
    TitleCompanyNormalizationNode,
)
from job_ftch.nodes.jobness import JobnessDecisionNode
from job_ftch.nodes.language_context import SourceContextNode
from job_ftch.nodes.lexical_evidence import LexicalEvidenceNode
from job_ftch.nodes.lifecycle import JobLifecycleNode
from job_ftch.nodes.llm_relevance_classification import LLMRelevanceClassificationNode
from job_ftch.nodes.match_scoring import MultiProfileMatchNode
from job_ftch.nodes.need_more_evidence import NeedMoreEvidenceNode
from job_ftch.nodes.ontology_snapshot import OntologySnapshotNode
from job_ftch.nodes.post_type import PostTypeClassificationNode
from job_ftch.nodes.quality import JobValidationNode, QualityScoringNode
from job_ftch.nodes.reranker import RerankerNode
from job_ftch.nodes.review_resolution import ReviewResolutionNode
from job_ftch.nodes.risk import RiskScoringNode
from job_ftch.nodes.routing import RoutingNode
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.nodes.semantic_prefilter import SemanticPrefilterNode
from job_ftch.nodes.tfidf_logreg_prefilter import TfidfLogregRelevancePrefilterNode

__all__ = [
    "CompensationParsingNode",
    "AcceptTemplatePresentationNode",
    "CandidateSegmentationNode",
    "CompletenessGateNode",
    "DedupNode",
    "DecisionExtractionNode",
    "DecisionNode",
    "DecisionPolicy",
    "EmbeddingNode",
    "ExtractionNode",
    "FullExtractionNode",
    "ExtractionValidationNode",
    "EvidenceFanOutNode",
    "EvidenceDecisionNode",
    "NeedMoreEvidenceNode",
    "GarbageFilterNode",
    "HardFilterNode",
    "JobAggregationNode",
    "JobLifecycleNode",
    "JobnessDecisionNode",
    "JobValidationNode",
    "LLMRelevanceClassificationNode",
    "LocationWorkModeNormalizationNode",
    "MultiProfileMatchNode",
    "OntologySnapshotNode",
    "ParallelScoringNode",
    "ProfileSemanticEvidenceNode",
    "LexicalEvidenceNode",
    "PostTypeClassificationNode",
    "QualityScoringNode",
    "RiskScoringNode",
    "ReviewResolutionNode",
    "RoutingNode",
    "RerankerNode",
    "SanitizeNode",
    "TfidfLogregRelevancePrefilterNode",
    "SemanticPrefilterNode",
    "SkillNormalizationNode",
    "SourceContextNode",
    "TitleCompanyNormalizationNode",
]


def __getattr__(name: str) -> object:
    """Load BGE-only nodes only when an opt-in graph requests them."""
    if name == "ParallelScoringNode":
        from job_ftch.nodes.parallel_scoring import ParallelScoringNode

        return ParallelScoringNode
    if name == "ProfileSemanticEvidenceNode":
        from job_ftch.nodes.profile_semantic_evidence import ProfileSemanticEvidenceNode

        return ProfileSemanticEvidenceNode
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
