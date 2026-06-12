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
    SkillNormalizationNode,
    TitleCompanyNormalizationNode,
)
from job_ftch.nodes.lifecycle import JobLifecycleNode
from job_ftch.nodes.language_context import SourceContextNode
from job_ftch.nodes.match_scoring import MultiProfileMatchNode
from job_ftch.nodes.post_type import PostTypeClassificationNode
from job_ftch.nodes.quality import JobValidationNode, QualityScoringNode
from job_ftch.nodes.risk import RiskScoringNode
from job_ftch.nodes.routing import RoutingNode
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.nodes.semantic_prefilter import SemanticPrefilterNode

__all__ = [
    "CompensationParsingNode",
    "DedupNode",
    "EmbeddingNode",
    "ExtractionNode",
    "ExtractionValidationNode",
    "HardFilterNode",
    "JobAggregationNode",
    "JobLifecycleNode",
    "JobValidationNode",
    "LocationWorkModeNormalizationNode",
    "MultiProfileMatchNode",
    "PostTypeClassificationNode",
    "QualityScoringNode",
    "RiskScoringNode",
    "RoutingNode",
    "SanitizeNode",
    "SemanticPrefilterNode",
    "SkillNormalizationNode",
    "SourceContextNode",
    "TitleCompanyNormalizationNode",
]
