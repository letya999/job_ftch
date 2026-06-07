"""Node implementations — each file implements the Node Protocol."""

from nodes.aggregation import JobAggregationNode
from nodes.dedup import DedupNode
from nodes.embedding import EmbeddingNode
from nodes.extraction import ExtractionNode
from nodes.extraction_validation import ExtractionValidationNode
from nodes.job_normalization import (
    CompensationParsingNode,
    LocationWorkModeNormalizationNode,
    TitleCompanyNormalizationNode,
)
from nodes.quality import JobValidationNode, QualityScoringNode
from nodes.relevance import AIRoleRelevanceNode
from nodes.sanitize import SanitizeNode
from nodes.triage import HeuristicTriageNode

__all__ = [
    "AIRoleRelevanceNode",
    "CompensationParsingNode",
    "DedupNode",
    "EmbeddingNode",
    "ExtractionNode",
    "ExtractionValidationNode",
    "HeuristicTriageNode",
    "JobAggregationNode",
    "JobValidationNode",
    "LocationWorkModeNormalizationNode",
    "QualityScoringNode",
    "SanitizeNode",
    "TitleCompanyNormalizationNode",
]
