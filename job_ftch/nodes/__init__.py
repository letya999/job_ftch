"""Node implementations — each file implements the Node Protocol."""

from job_ftch.nodes.aggregation import JobAggregationNode
from job_ftch.nodes.dedup import DedupNode
from job_ftch.nodes.embedding import EmbeddingNode
from job_ftch.nodes.extraction import ExtractionNode
from job_ftch.nodes.extraction_validation import ExtractionValidationNode
from job_ftch.nodes.hard_filter import HardFilterNode
from job_ftch.nodes.job_normalization import (
    CompensationParsingNode,
    LocationWorkModeNormalizationNode,
    TitleCompanyNormalizationNode,
)
from job_ftch.nodes.language_context import LanguageContextNode
from job_ftch.nodes.match_scoring import MultiProfileMatchNode
from job_ftch.nodes.post_type import PostTypeClassificationNode
from job_ftch.nodes.quality import JobValidationNode, QualityScoringNode
from job_ftch.nodes.relevance import AIRoleRelevanceNode
from job_ftch.nodes.risk import RiskScoringNode
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.nodes.semantic_prefilter import SemanticPrefilterNode
from job_ftch.nodes.triage import HeuristicTriageNode

__all__ = [
    "AIRoleRelevanceNode",
    "CompensationParsingNode",
    "DedupNode",
    "EmbeddingNode",
    "ExtractionNode",
    "ExtractionValidationNode",
    "HardFilterNode",
    "HeuristicTriageNode",
    "JobAggregationNode",
    "JobValidationNode",
    "LanguageContextNode",
    "LocationWorkModeNormalizationNode",
    "MultiProfileMatchNode",
    "PostTypeClassificationNode",
    "QualityScoringNode",
    "RiskScoringNode",
    "SanitizeNode",
    "SemanticPrefilterNode",
    "TitleCompanyNormalizationNode",
]
