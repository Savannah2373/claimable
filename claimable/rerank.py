"""Cross-encoder reranking. The hybrid retriever is recall-oriented; the
reranker reads (query, document) pairs jointly and re-scores for precision.

Local dev model: cross-encoder/ms-marco-MiniLM-L6-v2 (~90 MB, fast on CPU).
Deployment target is bge-reranker-v2-m3 via TEI — env-configurable so the
swap is a config change and the eval harness can compare the two.
"""

from __future__ import annotations

import os

_MODEL_NAME = os.environ.get("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L6-v2")

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(_MODEL_NAME)
    return _model


def rerank(query: str, documents: list[str]) -> list[float]:
    """Score each document against the query. Higher = more relevant."""
    if not documents:
        return []
    scores = _get_model().predict([(query, doc) for doc in documents])
    return [float(s) for s in scores]
