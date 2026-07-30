"""Compatibility projection for compiled ontologies.

Semantic ontology decisions live in ``ontology_compiler``.  This module keeps
the historical import path for callers while avoiding rule-based materializers.
"""

from __future__ import annotations

from job_ftch.application.ontology_compiler import materialize_compiled_ontology
from job_ftch.domain import CompiledOntology, MaterializedOntologyTerms, OntologyTermStat


def materialize_ontology_corpus(
    graphs: object,
) -> tuple[MaterializedOntologyTerms, tuple[OntologyTermStat, ...]]:
    """Materialize only already-compiled ontologies.

    The new graph-scoring implementation inferred semantic relevance with
    code-level heuristics.  That is intentionally no longer supported.
    """

    if isinstance(graphs, CompiledOntology):
        return materialize_compiled_ontology(graphs)
    msg = "materialize_ontology_corpus now requires a CompiledOntology"
    raise TypeError(msg)
